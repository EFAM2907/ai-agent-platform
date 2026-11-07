import { useEffect, useRef, useState } from "react";
import { MessageDetails } from "./MessageDetails";
import { renderMessageContent } from "./markdown";
import { RobotIcon } from "./RobotIcon";
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

function Icon({ d, size = 15 }: { d: string; size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}

const ICON_COPY = "M9 9h10v10H9zM5 15V5h10";
const ICON_UP = "M7 11v9H4v-9zM7 11l4-8a2 2 0 0 1 2 2v4h6a2 2 0 0 1 2 2l-1 6a2 2 0 0 1-2 2H7";
const ICON_DOWN = "M17 13V4h3v9zM17 13l-4 8a2 2 0 0 1-2-2v-4H5a2 2 0 0 1-2-2l1-6a2 2 0 0 1 2-2h11";
const ICON_INFO = "M12 16v-4M12 8h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0z";

const ACTION_STATUS_TEXT: Record<string, string> = {
  executed: "Ejecutada",
  failed: "Falló al ejecutarse",
  rejected: "Rechazada",
  expired: "Venció sin respuesta",
};

// Accion destructiva que el agente pidio: NO se ejecuto, necesita tu OK.
function ActionCard({
  action,
  onDecide,
}: {
  action: PendingAction;
  onDecide: (decision: "approve" | "reject") => void;
}) {
  const busy = action.status === "approved";
  const finished = action.status in ACTION_STATUS_TEXT;
  return (
    <div className={`action-card action-card--${action.status}`} role="group" aria-label="Acción pendiente de aprobación">
      <div className="action-card__title">
        Acción pendiente: <strong>{toolLabel(action.tool_name)}</strong>
      </div>
      <pre className="action-card__args">{JSON.stringify(action.arguments, null, 2)}</pre>
      {finished ? (
        <div className="action-card__status">{ACTION_STATUS_TEXT[action.status]}</div>
      ) : (
        <div className="action-card__buttons">
          <button type="button" className="btn btn--danger" disabled={busy} onClick={() => onDecide("approve")}>
            {busy ? "Ejecutando…" : "Aprobar y ejecutar"}
          </button>
          <button type="button" className="btn" disabled={busy} onClick={() => onDecide("reject")}>
            Rechazar
          </button>
        </div>
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
      {message.content ? renderMessageContent(message.content) : null}

      {message.pending_actions?.map((action) => (
        <ActionCard key={action.id} action={action} onDecide={(decision) => onDecideAction(action.id, decision)} />
      ))}

      {message.sources.length > 0 && (
        <div className="chat-msg__sources">
          <span className="chat-msg__sources-label">Fuentes</span>
          {message.sources.map((source) => (
            <span key={source} className="chip">
              {source}
            </span>
          ))}
        </div>
      )}

      {!isStreaming && message.content && isPersisted && (
        <div className="chat-msg__actions">
          <button type="button" className="icon-btn" onClick={() => void copy()} title="Copiar respuesta">
            <Icon d={ICON_COPY} />
            {copied && <span className="icon-btn__note">Copiado</span>}
          </button>
          <button
            type="button"
            className={`icon-btn${message.feedback === 1 ? " icon-btn--on" : ""}`}
            onClick={() => onRate(1)}
            title="Respuesta útil"
            aria-pressed={message.feedback === 1}
          >
            <Icon d={ICON_UP} />
          </button>
          <button
            type="button"
            className={`icon-btn${message.feedback === -1 ? " icon-btn--bad" : ""}`}
            onClick={() => onRate(-1)}
            title="Respuesta no útil"
            aria-pressed={message.feedback === -1}
          >
            <Icon d={ICON_DOWN} />
          </button>
          <button
            type="button"
            className={`icon-btn${showDetails ? " icon-btn--on" : ""}`}
            onClick={() => setShowDetails((value) => !value)}
            title="Cómo se generó esta respuesta"
            aria-expanded={showDetails}
          >
            <Icon d={ICON_INFO} />
            <span className="icon-btn__text">
              {message.trace ? `${(message.trace.latency_ms / 1000).toFixed(1)} s` : "Detalle"}
            </span>
          </button>
        </div>
      )}

      {showDetails && isPersisted && <MessageDetails messageId={message.id} />}
    </div>
  );
}

export function MainChat({ platformName, userName, organizationName, suggestions, chat }: MainChatProps) {
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
    element.style.height = `${Math.min(element.scrollHeight, 160)}px`;
  }, [inputValue]);

  function sendSuggestion(text: string) {
    setInputValue(text);
    // setInputValue es async (estado de React) -- handleSend lee
    // inputValue del closure del hook, no de este parametro, asi que
    // hace falta esperar al siguiente tick para que el estado ya
    // este actualizado antes de enviar.
    setTimeout(() => void handleSend(), 0);
  }

  const hasConversation = messages.length > 0;
  const lastMessage = messages[messages.length - 1];
  const waitingForFirstToken = isSending && lastMessage?.role === "assistant" && lastMessage.content === "";

  return (
    <main className="main-chat">
      {!hasConversation ? (
        <div className="main-chat__welcome">
          <div className="main-chat__welcome-icon">
            <RobotIcon size={40} />
          </div>
          <div className="main-chat__welcome-sub">Bienvenido a {platformName}</div>
          <h1 className="main-chat__welcome-title">
            {userName ? `Hola, ${userName}` : "¿En qué te ayudo?"}
          </h1>
          {organizationName && <p className="main-chat__welcome-org">{organizationName}</p>}

          <div className="main-chat__suggestions">
            {suggestions.map((text) => (
              <button key={text} type="button" className="main-chat__suggestion" onClick={() => sendSuggestion(text)}>
                {text}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="main-chat__thread" ref={bodyRef}>
          {messages.map((message, index) => {
            const isLast = index === messages.length - 1;
            if (message.role === "user") {
              return (
                <div key={message.id} className="chat-msg chat-msg--user">
                  {message.content}
                </div>
              );
            }
            // El placeholder vacío del asistente no se dibuja: debajo se muestra el estado.
            if (message.content === "" && isLast && isSending) return null;
            return (
              <AssistantMessage
                key={message.id}
                message={message}
                isStreaming={isSending && isLast}
                onRate={(rating) => void rateMessage(message.id, rating)}
                onDecideAction={(actionId, decision) => void decideAction(message.id, actionId, decision)}
              />
            );
          })}

          {isSending && (
            <div className="chat-status" role="status" aria-live="polite">
              <span className="chat-typing" aria-hidden="true">
                <span />
                <span />
                <span />
              </span>
              <span>
                {activeTool
                  ? `${toolLabel(activeTool)}…`
                  : waitingForFirstToken
                    ? "Pensando…"
                    : "Escribiendo…"}
              </span>
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="main-chat__error" role="alert">
          {error}
          {errorRef && <span className="main-chat__error-ref"> · Ref: {errorRef}</span>}
        </div>
      )}

      <div className="main-chat__inputbar">
        <textarea
          ref={inputRef}
          className="main-chat__input main-chat__input--multi"
          placeholder="Pregunta lo que quieras…  (Shift+Enter para salto de línea)"
          value={inputValue}
          rows={1}
          spellCheck={false}
          onChange={(event) => setInputValue(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isSending}
        />
        {isSending ? (
          <button type="button" className="main-chat__send main-chat__send--stop" onClick={stop} aria-label="Detener respuesta" title="Detener">
            <svg viewBox="0 0 24 24" fill="white" stroke="none">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        ) : (
          <button
            type="button"
            className="main-chat__send"
            onClick={() => void handleSend()}
            disabled={!inputValue.trim()}
            aria-label="Enviar mensaje"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
          </button>
        )}
      </div>
    </main>
  );
}
