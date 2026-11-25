import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowUp,
  BookOpen,
  Check,
  ChevronRight,
  Copy,
  FileText,
  Info,
  Loader2,
  Package,
  ShieldAlert,
  Sparkles,
  Square,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { MessageDetails } from "./MessageDetails";
import { renderMessageContent } from "./markdown";
import { toolLabel } from "./toolLabels";
import type { useChat } from "./useChat";
import type { ChatMessage, PendingAction } from "./types";

interface MainChatProps {
  platformName: string;
  userName: string | null;
  organizationName: string | null;
  // Sugerencias de inicio acordes al rol (un viewer no ve "crear usuario").
  suggestions: string[];
  chat: ReturnType<typeof useChat>;
}

const SUGGESTION_ICONS = [Sparkles, Package, FileText];

const ICON_PROPS = { size: 16, strokeWidth: 1.75 } as const;

// ---------- aprobacion humana ----------

const ACTION_FINAL_STATUS: Record<string, { text: string; icon: "check" | "alert" | "x" | "clock" }> = {
  executed: { text: "Ejecutada", icon: "check" },
  failed: { text: "Falló al ejecutarse", icon: "alert" },
  rejected: { text: "Rechazada", icon: "x" },
  expired: { text: "Venció sin respuesta", icon: "clock" },
};

function formatCountdown(msLeft: number): string {
  const total = Math.max(0, Math.ceil(msLeft / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

// Countdown to `expiresAt`. UI only: the backend stays the source of truth
// for whether the action can still be approved.
function useCountdown(expiresAt: string, active: boolean): number {
  const target = new Date(expiresAt).getTime();
  const [msLeft, setMsLeft] = useState(() => target - Date.now());
  useEffect(() => {
    setMsLeft(target - Date.now());
    if (!active) return;
    const id = window.setInterval(() => setMsLeft(target - Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [target, active]);
  return msLeft;
}

// Ids, tracking numbers and other numeric-looking values render in mono.
function isMonoValue(value: unknown): boolean {
  if (typeof value === "number") return true;
  if (typeof value !== "string") return false;
  return /\d/.test(value) && !/\s/.test(value) && value.length <= 64;
}

function formatArgValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

// Accion destructiva que el agente pidio: NO se ejecuto, necesita tu OK.
function ActionCard({
  action,
  onDecide,
}: {
  action: PendingAction;
  onDecide: (decision: "approve" | "reject") => void;
}) {
  const [showJson, setShowJson] = useState(false);
  const busy = action.status === "approved";
  const final = ACTION_FINAL_STATUS[action.status];
  const msLeft = useCountdown(action.expires_at, action.status === "pending");
  const expiredLocally = action.status === "pending" && msLeft <= 0;
  const entries = Object.entries(action.arguments);

  return (
    <div className={`action-card action-card--${action.status}`} role="group" aria-label="Acción pendiente de aprobación">
      <div className="action-card__header">
        <ShieldAlert {...ICON_PROPS} className="action-card__shield" aria-hidden="true" />
        <span className="action-card__heading">Acción pendiente de aprobación</span>
        {action.status === "pending" && (
          <span className={`action-card__timer mono${expiredLocally ? " action-card__timer--expired" : ""}`}>
            {expiredLocally ? "Vencida" : `Vence en ${formatCountdown(msLeft)}`}
          </span>
        )}
      </div>

      <div className="action-card__name">{toolLabel(action.tool_name)}</div>

      {entries.length > 0 && (
        <dl className="action-card__args">
          {entries.map(([key, value]) => (
            <div key={key} className="action-card__arg">
              <dt>{key}</dt>
              <dd className={isMonoValue(value) || typeof value === "object" ? "mono" : undefined}>
                {formatArgValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}

      <button
        type="button"
        className="action-card__json-toggle"
        aria-expanded={showJson}
        onClick={() => setShowJson((value) => !value)}
      >
        {showJson ? "Ocultar JSON" : "Ver JSON"}
      </button>
      {showJson && <pre className="action-card__json mono">{JSON.stringify(action.arguments, null, 2)}</pre>}

      {final ? (
        <div className={`action-card__status action-card__status--${action.status}`} role="status">
          {final.icon === "check" && <Check {...ICON_PROPS} />}
          {final.icon === "alert" && <AlertCircle {...ICON_PROPS} />}
          {final.icon === "x" && <X {...ICON_PROPS} />}
          {final.text}
        </div>
      ) : busy ? (
        <div className="action-card__status action-card__status--approved" role="status">
          <Loader2 {...ICON_PROPS} className="spin" />
          Ejecutando…
        </div>
      ) : (
        <div className="action-card__buttons">
          <button type="button" className="btn btn--danger" onClick={() => onDecide("approve")}>
            Aprobar y ejecutar
          </button>
          <button type="button" className="btn btn--ghost" onClick={() => onDecide("reject")}>
            Rechazar
          </button>
        </div>
      )}
    </div>
  );
}

// ---------- mensajes ----------

// Pasos: las herramientas que uso el agente para esta respuesta (colapsado por defecto).
function StepsList({ tools }: { tools: { name: string; ok: boolean }[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="steps">
      <button type="button" className="steps__toggle" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <ChevronRight size={14} strokeWidth={1.75} className={`steps__chevron${open ? " steps__chevron--open" : ""}`} />
        Pasos <span className="mono">({tools.length})</span>
      </button>
      {open && (
        <ul className="steps__list">
          {tools.map((tool, index) => (
            <li key={`${tool.name}-${index}`}>
              {tool.ok ? (
                <Check size={14} strokeWidth={1.75} className="steps__ok" aria-label="Correcto" />
              ) : (
                <X size={14} strokeWidth={1.75} className="steps__fail" aria-label="Con error" />
              )}
              {toolLabel(tool.name)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AssistantMessage({
  message,
  isStreaming,
  onRate,
  onDecideAction,
}: {
  message: ChatMessage;
  isStreaming: boolean;
  onRate: (rating: 1 | -1) => void;
  onDecideAction: (actionId: string, decision: "approve" | "reject") => void;
}) {
  const [copied, setCopied] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  // Mientras se genera, el id es local ("streaming-…"): aún no existe en el servidor.
  const isPersisted = !message.id.startsWith("streaming-") && !message.id.startsWith("local-");
  const steps = message.trace?.tools ?? [];

  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Sin permiso de portapapeles: no hay nada útil que mostrar.
    }
  }

  return (
    <div className="chat-msg chat-msg--assistant">
      {!isStreaming && steps.length > 0 && <StepsList tools={steps} />}

      {message.content ? renderMessageContent(message.content, { streaming: isStreaming }) : null}

      {message.pending_actions?.map((action) => (
        <ActionCard key={action.id} action={action} onDecide={(decision) => onDecideAction(action.id, decision)} />
      ))}

      {!isStreaming && message.sources.length > 0 && (
        <div className="chat-msg__sources">
          <span className="chat-msg__sources-label">Fuentes</span>
          {message.sources.map((source) => (
            <span key={source} className="source-chip">
              <BookOpen size={12} strokeWidth={1.75} aria-hidden="true" />
              {source}
            </span>
          ))}
        </div>
      )}

      {!isStreaming && message.content && isPersisted && (
        <div className="chat-actions">
          <button type="button" className="icon-btn" onClick={() => void copy()} title="Copiar respuesta" aria-label="Copiar respuesta">
            <Copy {...ICON_PROPS} />
            {copied && <span className="icon-btn__note">Copiado</span>}
          </button>
          <button
            type="button"
            className={`icon-btn${message.feedback === 1 ? " icon-btn--on" : ""}`}
            onClick={() => onRate(1)}
            title="Respuesta útil"
            aria-label="Respuesta útil"
            aria-pressed={message.feedback === 1}
          >
            <ThumbsUp {...ICON_PROPS} />
          </button>
          <button
            type="button"
            className={`icon-btn${message.feedback === -1 ? " icon-btn--bad" : ""}`}
            onClick={() => onRate(-1)}
            title="Respuesta no útil"
            aria-label="Respuesta no útil"
            aria-pressed={message.feedback === -1}
          >
            <ThumbsDown {...ICON_PROPS} />
          </button>
          <button
            type="button"
            className={`icon-btn icon-btn--text${showDetails ? " icon-btn--active" : ""}`}
            onClick={() => setShowDetails((value) => !value)}
            title="Cómo se generó esta respuesta"
            aria-expanded={showDetails}
          >
            <Info {...ICON_PROPS} />
            <span className="mono">{message.trace ? `${(message.trace.latency_ms / 1000).toFixed(1)} s` : "Detalle"}</span>
          </button>
        </div>
      )}

      {showDetails && isPersisted && <MessageDetails messageId={message.id} />}
    </div>
  );
}

// ---------- pantalla ----------

export function MainChat({ platformName: _platformName, userName, organizationName, suggestions, chat }: MainChatProps) {
  const {
    messages,
    inputValue,
    setInputValue,
    isSending,
    error,
    errorRef,
    activeTool,
    handleSend,
    handleKeyDown,
    stop,
    rateMessage,
    decideAction,
  } = chat;
  const bodyRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, isSending, activeTool]);

  // El campo crece con el texto (hasta un máximo) en vez de esconder lo que se escribe.
  useEffect(() => {
    const element = inputRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
  }, [inputValue]);

  // A suggestion fills the input and sends once React has committed that
  // value: handleSend reads inputValue from its own render's closure, so
  // calling it right after setInputValue would send an empty message.
  const [queuedSuggestion, setQueuedSuggestion] = useState<string | null>(null);
  useEffect(() => {
    if (queuedSuggestion !== null && inputValue === queuedSuggestion) {
      setQueuedSuggestion(null);
      void handleSend();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queuedSuggestion, inputValue]);

  function sendSuggestion(text: string) {
    setInputValue(text);
    setQueuedSuggestion(text);
  }

  const hasConversation = messages.length > 0;
  const lastMessage = messages[messages.length - 1];
  const waitingForFirstToken = isSending && lastMessage?.role === "assistant" && lastMessage.content === "";
  const firstName = userName?.trim().split(/\s+/)[0] ?? null;

  return (
    <main className="main-chat">
      <header className="chat-topbar">
        <h1 className="chat-topbar__title">Chat</h1>
        {organizationName && <span className="chat-topbar__org">{organizationName}</span>}
      </header>

      <div className="chat-scroll" ref={bodyRef}>
        <div className="chat-column">
          {!hasConversation ? (
            <div className="chat-empty">
              <h2 className="chat-empty__title">{firstName ? `Hola, ${firstName}` : "Hola"}</h2>
              <p className="chat-empty__sub">
                ¿En qué te ayudo hoy con la operación{organizationName ? ` de ${organizationName}` : ""}?
              </p>
              <div className="chat-empty__grid">
                {suggestions.slice(0, 3).map((text, index) => {
                  const SuggestionIcon = SUGGESTION_ICONS[index % SUGGESTION_ICONS.length];
                  return (
                    <button key={text} type="button" className="suggestion-card" onClick={() => sendSuggestion(text)}>
                      <SuggestionIcon {...ICON_PROPS} aria-hidden="true" />
                      <span>{text}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className="chat-thread">
              {messages.map((message, index) => {
                const isLast = index === messages.length - 1;
                if (message.role === "user") {
                  return (
                    <div key={message.id} className="chat-msg chat-msg--user">
                      {message.content}
                    </div>
                  );
                }
                const showStatus = isLast && isSending && (activeTool !== null || waitingForFirstToken);
                return (
                  <div key={message.id} className="chat-turn">
                    {showStatus && (
                      <div className="chat-status" role="status" aria-live="polite">
                        <Loader2 size={14} strokeWidth={1.75} className="spin chat-status__spinner" />
                        <span className="shimmer-text">
                          {activeTool ? `${toolLabel(activeTool)}…` : "Pensando…"}
                        </span>
                      </div>
                    )}
                    {/* El placeholder vacío del asistente no se dibuja: arriba se muestra el estado. */}
                    {!(message.content === "" && isLast && isSending && !message.pending_actions?.length) && (
                      <AssistantMessage
                        message={message}
                        isStreaming={isSending && isLast}
                        onRate={(rating) => void rateMessage(message.id, rating)}
                        onDecideAction={(actionId, decision) => void decideAction(message.id, actionId, decision)}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <div className="chat-composer-wrap">
        <div className="chat-column">
          {error && (
            <div className="chat-error" role="alert">
              <AlertCircle {...ICON_PROPS} />
              <span className="chat-error__text">{error}</span>
              {errorRef && <span className="chat-error__ref mono">Ref: {errorRef}</span>}
            </div>
          )}

          <div className="composer">
            <textarea
              ref={inputRef}
              className="composer__input"
              placeholder="Pregunta lo que quieras…"
              aria-label="Mensaje"
              value={inputValue}
              rows={1}
              spellCheck={false}
              onChange={(event) => setInputValue(event.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isSending}
            />
            <div className="composer__row">
              <span className="composer__hint">Enter para enviar · Shift + Enter nueva línea</span>
              {isSending ? (
                <button type="button" className="composer__stop" onClick={stop} aria-label="Detener respuesta" title="Detener">
                  <Square size={12} strokeWidth={1.75} fill="currentColor" />
                  Detener
                </button>
              ) : (
                <button
                  type="button"
                  className="composer__send"
                  onClick={() => void handleSend()}
                  disabled={!inputValue.trim()}
                  aria-label="Enviar mensaje"
                  title="Enviar"
                >
                  <ArrowUp size={16} strokeWidth={1.75} />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
