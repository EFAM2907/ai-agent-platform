import { AlertCircle, RefreshCw, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getTrace, getTraceStats, listTraces } from "./api";
import { TraceView, formatCost, formatMs } from "./MessageDetails";
import { PageShell } from "./PageShell";
import { toolLabel } from "./toolLabels";
import type { TraceDetail, TraceList, TraceStats } from "./types";

function formatDate(value: string): string {
  return new Date(value).toLocaleString("es-CO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

const PAGE_SIZE = 20;

// Horizontal bars built with plain CSS (no chart library). Widths are
// relative to the largest value in the list.
function BarList({
  rows,
  renderName,
}: {
  rows: { name: string; count: number; failed?: number }[];
  renderName?: (name: string) => string;
}) {
  const max = Math.max(1, ...rows.map((row) => row.count));
  return (
    <ul className="bars">
      {rows.map((row) => (
        <li key={row.name} className="bars__row">
          <span className="bars__name" title={row.name}>
            {renderName ? renderName(row.name) : row.name}
          </span>
          <span className="bars__value mono">{row.count}</span>
          <span className="bars__track" role="img" aria-label={`${row.count} de ${max}`}>
            <span className="bars__fill" style={{ width: `${(row.count / max) * 100}%` }} />
          </span>
          {row.failed ? <span className="bars__failed mono">{row.failed} fallaron</span> : null}
        </li>
      ))}
    </ul>
  );
}

// Right-side panel with the detail of one turn. Closes with Esc, the close
// button or a click on the backdrop; full screen on mobile.
function TraceDrawer({ trace, onClose }: { trace: TraceDetail; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [onClose]);

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Detalle del turno"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="drawer__head">
          <h2 className="drawer__title">Detalle del turno</h2>
          <span className="drawer__sub mono">{formatDate(trace.created_at)}</span>
          <button ref={closeRef} type="button" className="drawer__close" onClick={onClose} aria-label="Cerrar detalle">
            <X size={16} strokeWidth={1.75} />
          </button>
        </div>
        <div className="drawer__body">
          <TraceView trace={trace} />
        </div>
      </aside>
    </div>
  );
}

export function TracesView() {
  const [days, setDays] = useState(7);
  const [status, setStatus] = useState<"" | "ok" | "error">("");
  const [page, setPage] = useState(0);
  const [stats, setStats] = useState<TraceStats | null>(null);
  const [list, setList] = useState<TraceList | null>(null);
  const [selected, setSelected] = useState<TraceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [statsData, listData] = await Promise.all([
        getTraceStats(days),
        listTraces({ status: status || undefined, skip: page * PAGE_SIZE, limit: PAGE_SIZE }),
      ]);
      setStats(statsData);
      setList(listData);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudieron cargar las trazas");
    }
  }, [days, status, page]);

  useEffect(() => {
    void load();
  }, [load]);

  async function open(id: string) {
    try {
      setSelected(await getTrace(id));
    } catch {
      setError("No se pudo abrir esa traza.");
    }
  }

  const closeDrawer = useCallback(() => setSelected(null), []);

  const totalPages = list ? Math.max(1, Math.ceil(list.total / PAGE_SIZE)) : 1;
  const errorRate = stats && stats.turns > 0 ? Math.round((stats.errors / stats.turns) * 100) : 0;

  return (
    <PageShell
      title="Trazabilidad"
      description="Cada respuesta del asistente: qué modelo la generó, qué consultó y cuánto costó. Solo administradores."
      actions={
        <>
          <label className="field-inline">
            Período
            <select
              value={days}
              onChange={(event) => {
                setDays(Number(event.target.value));
                setPage(0);
              }}
            >
              <option value={1}>Último día</option>
              <option value={7}>Últimos 7 días</option>
              <option value={30}>Últimos 30 días</option>
              <option value={90}>Últimos 90 días</option>
            </select>
          </label>
          <button type="button" className="btn btn--sm" onClick={() => void load()}>
            <RefreshCw size={14} strokeWidth={1.75} />
            Actualizar
          </button>
        </>
      }
    >
      {error && (
        <div className="notice notice--error" role="alert">
          <AlertCircle size={16} strokeWidth={1.75} />
          {error}
        </div>
      )}

      <div className="stat-cards">
        <div className="stat-card">
          <div className="stat-card__value">{stats?.turns ?? "—"}</div>
          <div className="stat-card__label">Turnos</div>
        </div>
        <div className="stat-card">
          <div className={`stat-card__value${stats && stats.errors > 0 ? " text-bad" : ""}`}>
            {stats ? `${errorRate}%` : "—"}
          </div>
          <div className="stat-card__label">
            Con error (<span className="mono">{stats?.errors ?? 0}</span>)
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-card__value">{stats ? formatMs(stats.avg_latency_ms) : "—"}</div>
          <div className="stat-card__label">
            Tiempo medio · p95 <span className="mono">{stats ? formatMs(stats.p95_latency_ms) : "—"}</span>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-card__value">
            {stats ? (stats.input_tokens + stats.output_tokens).toLocaleString("es-CO") : "—"}
          </div>
          <div className="stat-card__label">
            Tokens · <span className="mono">{stats ? formatCost(stats.cost_usd) : "—"}</span>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-card__value">
            {stats ? (
              <>
                <span className="text-ok">
                  <ThumbsUp size={16} strokeWidth={1.75} aria-label="Positivas" /> {stats.feedback_positive}
                </span>{" "}
                <span className="text-bad">
                  <ThumbsDown size={16} strokeWidth={1.75} aria-label="Negativas" /> {stats.feedback_negative}
                </span>
              </>
            ) : (
              "—"
            )}
          </div>
          <div className="stat-card__label">Valoraciones</div>
        </div>
      </div>

      {stats && (stats.by_model.length > 0 || stats.by_tool.length > 0) && (
        <div className="two-cols">
          <section className="card">
            <h2>Modelos</h2>
            <BarList rows={stats.by_model} />
          </section>
          <section className="card">
            <h2>Herramientas más usadas</h2>
            {stats.by_tool.length === 0 ? (
              <p className="muted">Ninguna herramienta en este período.</p>
            ) : (
              <BarList rows={stats.by_tool.slice(0, 8)} renderName={toolLabel} />
            )}
          </section>
        </div>
      )}

      <section className="card">
        <div className="card__head">
          <h2>
            Turnos recientes <span className="mono">({list?.total ?? 0})</span>
          </h2>
          <label className="field-inline">
            Estado
            <select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value as "" | "ok" | "error");
                setPage(0);
              }}
            >
              <option value="">Todos</option>
              <option value="ok">Correctos</option>
              <option value="error">Con error</option>
            </select>
          </label>
        </div>

        {!list ? (
          <p className="muted">Cargando…</p>
        ) : list.traces.length === 0 ? (
          <p className="muted">Todavía no hay turnos en este período.</p>
        ) : (
          <div className="table-wrap">
            <table className="table table--clickable">
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Estado</th>
                  <th>Modelo</th>
                  <th className="table__num">Tiempo</th>
                  <th className="table__num">Tokens</th>
                  <th className="table__num">KB</th>
                  <th className="table__num">Herram.</th>
                </tr>
              </thead>
              <tbody>
                {list.traces.map((trace) => (
                  <tr
                    key={trace.id}
                    aria-selected={selected?.id === trace.id}
                    onClick={() => void open(trace.id)}
                    tabIndex={0}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") void open(trace.id);
                    }}
                  >
                    <td className="mono">{formatDate(trace.created_at)}</td>
                    <td>
                      <span className={`badge ${trace.status === "error" ? "badge--bad" : "badge--ok"}`}>
                        {trace.status === "error" ? trace.error_type ?? "error" : "ok"}
                      </span>
                    </td>
                    <td className="mono">{trace.model ?? "—"}</td>
                    <td className="mono table__num">{formatMs(trace.latency_ms)}</td>
                    <td className="mono table__num">{trace.input_tokens + trace.output_tokens}</td>
                    <td className="mono table__num">{trace.kb_chunks}</td>
                    <td className="mono table__num">{trace.tools_used}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {list && list.total > PAGE_SIZE && (
          <div className="row row--between">
            <button type="button" className="btn btn--sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
              ← Anteriores
            </button>
            <span className="muted">
              Página <span className="mono">{page + 1}</span> de <span className="mono">{totalPages}</span>
            </span>
            <button type="button" className="btn btn--sm" disabled={page + 1 >= totalPages} onClick={() => setPage((p) => p + 1)}>
              Siguientes →
            </button>
          </div>
        )}
      </section>

      {selected && <TraceDrawer trace={selected} onClose={closeDrawer} />}
    </PageShell>
  );
}
