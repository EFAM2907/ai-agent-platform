import { useEffect, useRef } from "react";
import { renderMessageContent } from "./markdown";
import { RobotIcon } from "./RobotIcon";
import type { useChat } from "./useChat";

interface MainChatProps {
  platformName: string;
  userName: string | null;
  organizationName: string | null;
  chat: ReturnType<typeof useChat>;
}

// Sugerencias pensadas para el dominio real (Inter Rapidísimo), no
// genericas -- un click llena el input con la pregunta y la envia,
// mismo patron que las "starter cards" de la referencia visual que
// pidio el usuario, pero con contenido propio del proyecto.
const SUGGESTIONS = [
  "¿Cuál es el estado del envío 854321?",
  "¿Cuál es la política de retrasos para envíos nacionales?",
  "Agrega un nuevo usuario a la organización",
];

export function MainChat({ platformName, userName, organizationName, chat }: MainChatProps) {
  const { messages, inputValue, setInputValue, isSending, error, handleSend, handleKeyDown } = chat;
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, isSending]);

  function sendSuggestion(text: string) {
    setInputValue(text);
    // setInputValue es async (estado de React) -- handleSend lee
    // inputValue del closure del hook, no de este parametro, asi que
    // hace falta esperar al siguiente tick para que el estado ya
    // este actualizado antes de enviar.
    setTimeout(() => void handleSend(), 0);
  }

  const hasConversation = messages.length > 0;

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
            {SUGGESTIONS.map((text) => (
              <button key={text} type="button" className="main-chat__suggestion" onClick={() => sendSuggestion(text)}>
                {text}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="main-chat__thread" ref={bodyRef}>
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
        </div>
      )}

      {error && <div className="main-chat__error">{error}</div>}

      <div className="main-chat__inputbar">
        <input
          className="main-chat__input"
          type="text"
          placeholder="Pregunta lo que quieras…"
          value={inputValue}
          onChange={(event) => setInputValue(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isSending}
        />
        <button
          type="button"
          className="main-chat__send"
          onClick={() => void handleSend()}
          disabled={isSending || !inputValue.trim()}
          aria-label="Enviar mensaje"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12h14M13 6l6 6-6 6" />
          </svg>
        </button>
      </div>
    </main>
  );
}
