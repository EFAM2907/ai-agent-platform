import { useEffect, useRef, useState } from "react";
import { ApiError, SessionExpiredError, clearTokens, getChatSession, streamChatMessage } from "./api";
import { RobotIcon } from "./RobotIcon";
import { renderMessageContent } from "./markdown";
import type { ChatMessage } from "./types";

interface ChatWidgetProps {
  userName: string | null;
  onSessionExpired: () => void;
}

// Clave de localStorage separada del token de auth -- perder la
// sesion de chat al refrescar la pagina no deberia pasar solo porque
// alguien recargo el navegador durante la demo.
const SESSION_STORAGE_KEY = "chat_session_id";

export function ChatWidget({ userName, onSessionExpired }: ChatWidgetProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasRestoredHistory, setHasRestoredHistory] = useState(false);
  const [showWelcomeBubble, setShowWelcomeBubble] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Burbuja de bienvenida junto al boton flotante -- aparece sola poco
  // despues de cargar la pagina para que el usuario la note sin tener
  // que abrir el chat primero.
  useEffect(() => {
    if (!userName) return;
    const timer = setTimeout(() => setShowWelcomeBubble(true), 600);
    return () => clearTimeout(timer);
  }, [userName]);

  // Restaura la conversacion anterior (si existe) la primera vez que
  // se abre el widget -- no al montar el componente, para no gastar
  // una llamada a la API si el usuario nunca abre el chat.
  useEffect(() => {
    if (!isOpen || hasRestoredHistory) return;
    setHasRestoredHistory(true);

    const storedSessionId = localStorage.getItem(SESSION_STORAGE_KEY);
    if (!storedSessionId) return;

    getChatSession(storedSessionId)
      .then((session) => {
        setSessionId(session.id);
        setMessages(session.messages);
      })
      .catch((err) => {
        if (err instanceof SessionExpiredError) {
          clearTokens();
          onSessionExpired();
          return;
        }
        // Sesion vieja invalida (o de otro tenant/usuario) -- se
        // descarta en silencio y se empieza una conversacion nueva,
        // no se le muestra un error al usuario por esto.
        localStorage.removeItem(SESSION_STORAGE_KEY);
      });
  }, [isOpen, hasRestoredHistory, onSessionExpired]);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, isSending]);

  async function handleSend() {
    const trimmed = inputValue.trim();
    if (!trimmed || isSending) return;

    const optimisticUserMessage: ChatMessage = {
      id: `local-${Date.now()}`,
      role: "user",
      content: trimmed,
      sources: [],
      created_at: new Date().toISOString(),
    };
    // Placeholder del mensaje del asistente -- arranca vacio y se va
    // llenando con cada "delta" que llega del stream, en vez de
    // aparecer de golpe al final de los ~25s que puede tardar la
    // generacion completa.
    const assistantMessageId = `streaming-${Date.now()}`;
    let assistantContent = "";
    let assistantSources: string[] = [];

    setMessages((prev) => [
      ...prev,
      optimisticUserMessage,
      { id: assistantMessageId, role: "assistant", content: "", sources: [], created_at: new Date().toISOString() },
    ]);
    setInputValue("");
    setIsSending(true);
    setError(null);

    try {
      for await (const event of streamChatMessage(trimmed, sessionId)) {
        if (event.type === "start") {
          setSessionId(event.data.session_id);
          localStorage.setItem(SESSION_STORAGE_KEY, event.data.session_id);
          assistantSources = event.data.sources;
        } else if (event.type === "delta") {
          assistantContent += event.data.content;
          const liveContent = assistantContent;
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantMessageId ? { ...m, content: liveContent } : m)),
          );
        } else if (event.type === "done") {
          const { message_id, created_at } = event.data;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessageId
                ? { ...m, id: message_id, sources: assistantSources, created_at }
                : m,
            ),
          );
        } else if (event.type === "error") {
          throw new ApiError(500, event.data.detail);
        }
      }
    } catch (err) {
      if (err instanceof SessionExpiredError) {
        clearTokens();
        onSessionExpired();
        return;
      }
      const message =
        err instanceof ApiError ? err.message : "No se pudo enviar el mensaje. Intenta de nuevo.";
      setError(message);
      // Si nunca llego texto util, quita el placeholder vacio en vez de
      // dejar una burbuja en blanco colgada en la conversacion.
      if (!assistantContent) {
        setMessages((prev) => prev.filter((m) => m.id !== assistantMessageId));
      }
    } finally {
      setIsSending(false);
    }
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      void handleSend();
    }
  }

  return (
    <>
      {isOpen && (
        <div className="chat-widget" role="dialog" aria-label="Chat de soporte">
          <div className="chat-widget__header">
            <div className="chat-widget__avatar">
              <RobotIcon size={17} />
            </div>
            <div>
              <div className="chat-widget__title">Soporte</div>
              <div className="chat-widget__status">
                <span className="chat-widget__pulse" />
                En línea
              </div>
            </div>
            <button
              type="button"
              className="chat-widget__close"
              onClick={() => setIsOpen(false)}
              aria-label="Cerrar chat"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round">
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </div>

          <div className="chat-widget__body" ref={bodyRef}>
            {messages.length === 0 && (
              <div className="chat-msg chat-msg--assistant">
                {userName ? `¡Hola, ${userName}! ` : "¡Hola! "}
                Puedo ayudarte con acceso, permisos, integraciones o límites de tu
                plan. ¿En qué te ayudo?
              </div>
            )}
            {messages.map((message) => (
              <div key={message.id} className={`chat-msg chat-msg--${message.role}`}>
                {message.role === "assistant" ? renderMessageContent(message.content) : message.content}
                {message.sources.length > 0 && (
                  <span className="chat-msg__cite">
                    <b>Fuente:</b> {message.sources.join(", ")}
                  </span>
                )}
              </div>
            ))}
            {isSending && messages[messages.length - 1]?.content === "" && (
              <div className="chat-typing" aria-label="Escribiendo respuesta">
                <span />
                <span />
                <span />
              </div>
            )}
            {error && <div className="chat-widget__error">{error}</div>}
          </div>

          <div className="chat-widget__inputbar">
            <input
              className="chat-widget__input"
              type="text"
              placeholder="Escribe tu pregunta…"
              value={inputValue}
              onChange={(event) => setInputValue(event.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isSending}
            />
            <button
              type="button"
              className="chat-widget__send"
              onClick={() => void handleSend()}
              disabled={isSending || !inputValue.trim()}
              aria-label="Enviar mensaje"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </button>
          </div>
        </div>
      )}

      {!isOpen && showWelcomeBubble && userName && (
        <div className="chat-welcome-bubble" role="status">
          <button
            type="button"
            className="chat-welcome-bubble__close"
            onClick={() => setShowWelcomeBubble(false)}
            aria-label="Cerrar mensaje de bienvenida"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
          ¡Bienvenido, {userName}!
        </div>
      )}

      <button
        type="button"
        className="chat-fab"
        onClick={() => {
          setIsOpen((open) => !open);
          setShowWelcomeBubble(false);
        }}
        aria-label={isOpen ? "Cerrar chat" : "Abrir chat de soporte"}
      >
        <RobotIcon size={26} />
      </button>
    </>
  );
}
