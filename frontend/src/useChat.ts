import { useEffect, useState } from "react";
import {
  ApiError,
  SESSION_STORAGE_KEY,
  SessionExpiredError,
  clearTokens,
  getChatSession,
  streamChatMessage,
} from "./api";
import type { ChatMessage } from "./types";

export function useChat(onSessionExpired: () => void) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasRestoredHistory, setHasRestoredHistory] = useState(false);

  // Restaura la ultima conversacion (si existe) una sola vez al montar
  // -- a diferencia del ChatWidget original, aca no espera a que se
  // "abra" nada: en la pagina nueva el chat ES la pagina.
  useEffect(() => {
    if (hasRestoredHistory) return;
    setHasRestoredHistory(true);

    const storedSessionId = localStorage.getItem(SESSION_STORAGE_KEY);
    if (!storedSessionId) return;
    void loadSession(storedSessionId, { silent: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasRestoredHistory]);

  async function loadSession(id: string, opts?: { silent?: boolean }) {
    try {
      const session = await getChatSession(id);
      setSessionId(session.id);
      setMessages(session.messages);
      setError(null);
      localStorage.setItem(SESSION_STORAGE_KEY, session.id);
    } catch (err) {
      if (err instanceof SessionExpiredError) {
        clearTokens();
        onSessionExpired();
        return;
      }
      // Sesion vieja invalida (o de otro tenant/usuario) -- se
      // descarta en silencio si es la restauracion automatica al
      // cargar la pagina; si el usuario la pidio a mano desde el
      // historial, sí le mostramos el error.
      localStorage.removeItem(SESSION_STORAGE_KEY);
      if (!opts?.silent) {
        setError("No se pudo abrir esa conversación.");
      }
    }
  }

  function startNewSession() {
    setSessionId(null);
    setMessages([]);
    setError(null);
    localStorage.removeItem(SESSION_STORAGE_KEY);
  }

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
    // llenando con cada "delta" que llega del stream.
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

  return {
    messages,
    sessionId,
    inputValue,
    setInputValue,
    isSending,
    error,
    handleSend,
    handleKeyDown,
    loadSession,
    startNewSession,
  };
}
