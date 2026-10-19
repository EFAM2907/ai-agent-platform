import { useCallback, useEffect, useState } from "react";
import { ApiError, getTrace, getTraceStats, listTraces } from "./api";
import { TraceView, formatCost, formatMs } from "./MessageDetails";
import type { TraceDetail, TraceList, TraceStats } from "./types";

function formatDate(value: string): string {
  return new Date(value).toLocaleString("es-CO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

const PAGE_SIZE = 20;

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

  const totalPages = list ? Math.max(1, Math.ceil(list.total / PAGE_SIZE)) : 1;
  const errorRate = stats && stats.turns > 0 ? Math.round((stats.errors / stats.turns) * 100) : 0;

  return (
    <div className="view">
      <header className="view__header">
        <h1>Trazabilidad</h1>
        <p>Cada respuesta del asistente: qué modelo la generó, qué consultó y cuánto costó. Solo administradores.</p>
      </header>

      {error && <div className="login-error">{error}</div>}

      <div className="row row--between">
        <label className="field-inline">
          Período
          <select value={days} onChange={(event) => { setDays(Number(event.target.value)); setPage(0); }}>
            <option value={1}>Último día</option>
            <option value={7}>Últimos 7 días</option>
            <option value={30}>Últimos 30 días</option>
            <option value={90}>Últimos 90 días</option>
          </select>
        </label>
        <button type="button" className="login-secondary" onClick={() => void load()}>
          Actualizar
        </button>
      </div>

      <div className="stat-cards">
        <div className="stat-card">
          <div className="stat-card__value">{stats?.turns ?? "—"}</div>
          <div className="stat-card__label">Turnos</div>
        </div>
        <div className="stat-card">
          <div className={`stat-card__value${stats && stats.errors > 0 ? " text-bad" : ""}`}>
            {stats ? `${errorRate}%` : "—"}
          </div>
          <div className="stat-card__label">Con error ({stats?.errors ?? 0})</div>
        </div>
        <div className="stat-card">
          <div className="stat-card__value">{stats ? formatMs(stats.avg_latency_ms) : "—"}</div>
          <div className="stat-card__label">Tiempo medio · p95 {stats ? formatMs(stats.p95_latency_ms) : "—"}</div>
        </div>
        <div className="stat-card">
          <div className="stat-card__value">
            {stats ? (stats.input_tokens + stats.output_tokens).toLocaleString("es-CO") : "—"}
          </div>
          <div className="stat-card__label">Tokens · {stats ? formatCost(stats.cost_usd) : "—"}</div>
        </div>
        <div className="stat-card">
          <div className="stat-card__value">
            {stats ? (
              <>
                <span className="text-ok">👍 {stats.feedback_positive}</span>{" "}
                <span className="text-bad">👎 {stats.feedback_negative}</span>
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
            <ul className="plain-list">
              {stats.by_model.map((row) => (
                <li key={row.name}>
                  {row.name} <span className="muted">· {row.count} turnos</span>
                </li>
              ))}
            </ul>
          </section>
          <section className="card">
            <h2>Herramientas más usadas</h2>
            {stats.by_tool.length === 0 ? (
              <p className="muted">Ninguna herramienta en este período.</p>
            ) : (
              <ul className="plain-list">
                {stats.by_tool.slice(0, 8).map((row) => (
                  <li key={row.name}>
                    {row.name} <span className="muted">· {row.count}</span>
                    {row.failed > 0 && <span className="text-bad"> · {row.failed} fallaron</span>}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}

      <section className="card">
        <div className="card__head">
          <h2>Turnos recientes ({list?.total ?? 0})</h2>
          <label className="field-inline">
            Estado
            <select value={status} onChange={(event) => { setStatus(event.target.value as "" | "ok" | "error"); setPage(0); }}>
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
                  <th>Tiempo</th>
                  <th>Tokens</th>
                  <th>KB</th>
                  <th>Herram.</th>
                </tr>
              </thead>
              <tbody>
                {list.traces.map((trace) => (
                  <tr key={trace.id} onClick={() => void open(trace.id)} tabIndex={0} onKeyDown={(event) => { if (event.key === "Enter") void open(trace.id); }}>
                    <td>{formatDate(trace.created_at)}</td>
                    <td>
                      <span className={`chip${trace.status === "error" ? " chip--bad" : " chip--ok"}`}>
                        {trace.status === "error" ? trace.error_type ?? "error" : "ok"}
                      </span>
                    </td>
                    <td>{trace.model ?? "—"}</td>
                    <td>{formatMs(trace.latency_ms)}</td>
                    <td>{trace.input_tokens + trace.output_tokens}</td>
                    <td>{trace.kb_chunks}</td>
                    <td>{trace.tools_used}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {list && list.total > PAGE_SIZE && (
          <div className="row row--between">
            <button type="button" className="login-secondary" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
              ← Anteriores
            </button>
            <span className="muted">
              Página {page + 1} de {totalPages}
            </span>
            <button type="button" className="login-secondary" disabled={page + 1 >= totalPages} onClick={() => setPage((p) => p + 1)}>
              Siguientes →
            </button>
          </div>
        )}
      </section>

      {selected && (
        <div className="modal-overlay" onClick={() => setSelected(null)}>
          <div className="modal-card modal-card--wide" onClick={(event) => event.stopPropagation()}>
            <h2>Detalle del turno</h2>
            <p className="muted">{formatDate(selected.created_at)}</p>
            <TraceView trace={selected} />
            <div className="modal-actions">
              <button type="button" className="login-secondary" onClick={() => setSelected(null)}>
                Cerrar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
