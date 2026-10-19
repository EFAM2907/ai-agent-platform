import { useEffect, useState } from "react";
import { ApiError, getMessageTrace } from "./api";
import { toolLabel } from "./toolLabels";
import type { TraceDetail } from "./types";

const ROUTE_REASONS: Record<string, string> = {
  trivial: "mensaje corto (no se consultó al clasificador)",
  classified_cheap: "pregunta sencilla → modelo económico",
  classified_expensive: "pregunta compleja → modelo avanzado",
  classifier_failed: "falló el clasificador → modelo avanzado por seguridad",
  router_error: "error del router → modelo avanzado por seguridad",
};

export function formatMs(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function formatCost(cost: number | null): string {
  if (cost === null) return "—";
  return cost < 0.01 ? `<$0.01` : `$${cost.toFixed(2)}`;
}

// Cómo se construyó UNA respuesta: qué modelo, qué fragmentos de la base de
// conocimiento entraron al contexto, qué herramientas se ejecutaron y
// cuánto costó. Lo ve quien recibió la respuesta y los administradores.
export function TraceView({ trace }: { trace: TraceDetail }) {
  const chunks = trace.retrieval.chunks ?? [];
  return (
    <div className="trace">
      <dl className="trace__grid">
        <div>
          <dt>Modelo</dt>
          <dd>{trace.model ?? "—"}</dd>
        </div>
        <div>
          <dt>Tiempo total</dt>
          <dd>{formatMs(trace.latency_ms)}</dd>
        </div>
        <div>
          <dt>Tokens</dt>
          <dd>
            {trace.input_tokens} → {trace.output_tokens}
          </dd>
        </div>
        <div>
          <dt>Costo estimado</dt>
          <dd>{formatCost(trace.cost_usd)}</dd>
        </div>
      </dl>

      {trace.route.reason && (
        <p className="trace__line">
          <b>Modelo elegido:</b> {ROUTE_REASONS[trace.route.reason] ?? trace.route.reason}
        </p>
      )}

      <div className="trace__block">
        <div className="trace__title">Base de conocimiento</div>
        {trace.retrieval.error ? (
          <p className="trace__line trace__line--warn">
            La búsqueda falló y se respondió sin contexto: {trace.retrieval.error}
          </p>
        ) : chunks.length === 0 ? (
          <p className="trace__line">No había nada relevante en la base de conocimiento.</p>
        ) : (
          <ul className="trace__list">
            {chunks.map((chunk) => (
              <li key={chunk.chunk_id}>
                {chunk.title} <span className="trace__muted">· relevancia {chunk.score.toFixed(3)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {trace.tool_calls.length > 0 && (
        <div className="trace__block">
          <div className="trace__title">Herramientas</div>
          <ul className="trace__list">
            {trace.tool_calls.map((call, index) => (
              <li key={index}>
                <span className={call.ok ? "trace__ok" : "trace__fail"}>{call.ok ? "✓" : "✗"}</span>{" "}
                {toolLabel(call.name)} <span className="trace__muted">· {formatMs(call.duration_ms)}</span>
                {Object.keys(call.arguments).length > 0 && (
                  <code className="trace__args">{JSON.stringify(call.arguments)}</code>
                )}
                {call.error && <div className="trace__line trace__line--warn">{call.error}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {trace.status === "error" && (
        <p className="trace__line trace__line--warn">
          Este turno falló: {trace.error_type}
          {trace.error_message ? ` — ${trace.error_message}` : ""}
        </p>
      )}

      <div className="trace__ref">Ref: {trace.trace_id.slice(0, 8)}</div>
    </div>
  );
}

// Carga la traza de un mensaje al abrirla (no antes: la mayoría de las
// respuestas nunca se inspeccionan).
export function MessageDetails({ messageId }: { messageId: string }) {
  const [trace, setTrace] = useState<TraceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMessageTrace(messageId)
      .then((data) => {
        if (!cancelled) setTrace(data);
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof ApiError && err.status === 404 ? "Esta respuesta no tiene traza." : "No se pudo cargar el detalle.");
      });
    return () => {
      cancelled = true;
    };
  }, [messageId]);

  if (error) return <div className="trace trace__line">{error}</div>;
  if (!trace) return <div className="trace trace__line">Cargando…</div>;
  return <TraceView trace={trace} />;
}
