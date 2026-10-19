import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  SESSION_STORAGE_KEY,
  SessionExpiredError,
  clearTokens,
  decidePendingAction,
  getChatSession,
  listPendingActions,
  setMessageFeedback,
  streamChatMessage,
} from "./api";
import type { ChatMessage, PendingAction } from "./types";

export function useChat(onSessionExpired: () => void) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Referencia de la traza del último fallo, para que quien reporte el
  // problema pueda citarla ("Ref: 3f9a1c2b").
  const [errorRef, setErrorRef] = useState<string | null>(null);
  // Herramienta que el agente está ejecutando ahora mismo (null = ninguna).
  const [activeTool, setActiveTool] = useState<string | null>(null);
  const [hasRestoredHistory, setHasRestoredHistory] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

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
      let restored = session.messages;
      // Acciones que quedaron esperando tu aprobacion en esta conversacion:
      // se muestran en la ultima respuesta del asistente.
      try {
        const pending = await listPendingActions(session.id);
        const lastAssistant = [...restored].reverse().find((m) => m.role === "assistant");
        if (pending.length && lastAssistant) {
          restored = restored.map((m) =>
            m.id === lastAssistant.id ? { ...m, pending_actions: pending } : m,
          );
        }
      } catch {
        // sin acciones pendientes visibles; no bloquea abrir la conversacion
      }
      setMessages(restored);
      setError(null);
      setErrorRef(null);
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
    abortRef.current?.abort();
    setSessionId(null);
    setMessages([]);
    setError(null);
    setErrorRef(null);
    setActiveTool(null);
    localStorage.removeItem(SESSION_STORAGE_KEY);
  }

  // Corta la respuesta en curso. El servidor no guarda una respuesta a
  // medias, así que la parte ya mostrada es solo de esta pantalla.
  function stop() {
    abortRef.current?.abort();
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
    setErrorRef(null);
    setActiveTool(null);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      for await (const event of streamChatMessage(trimmed, sessionId, controller.signal)) {
        if (event.type === "start") {
          setSessionId(event.data.session_id);
          localStorage.setItem(SESSION_STORAGE_KEY, event.data.session_id);
          assistantSources = event.data.sources;
        } else if (event.type === "tool") {
          setActiveTool(event.data.phase === "start" ? event.data.name : null);
        } else if (event.type === "confirmation_required") {
          const action: PendingAction = { ...event.data.action, status: "pending" };
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessageId
                ? { ...m, pending_actions: [...(m.pending_actions ?? []), action] }
                : m,
            ),
          );
        } else if (event.type === "delta") {
          setActiveTool(null);
          assistantContent += event.data.content;
          const liveContent = assistantContent;
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantMessageId ? { ...m, content: liveContent } : m)),
          );
        } else if (event.type === "done") {
          const { message_id, created_at, trace } = event.data;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessageId
                ? { ...m, id: message_id, sources: assistantSources, created_at, trace }
                : m,
            ),
          );
        } else if (event.type === "error") {
          if (event.data.trace_id) setErrorRef(event.data.trace_id.slice(0, 8));
          throw new ApiError(500, event.data.detail);
        }
      }
    } catch (err) {
      if (err instanceof SessionExpiredError) {
        clearTokens();
        onSessionExpired();
        return;
      }
      if (controller.signal.aborted) {
        setError("Detuviste la respuesta.");
      } else {
        const message =
          err instanceof ApiError ? err.message : "No se pudo enviar el mensaje. Intenta de nuevo.";
        setError(message);
      }
      if (!assistantContent) {
        setMessages((prev) => prev.filter((m) => m.id !== assistantMessageId));
      }
    } finally {
      setIsSending(false);
      setActiveTool(null);
      abortRef.current = null;
    }
  }

  // Valorar una respuesta: se refleja al instante y se revierte si el
  // servidor la rechaza. Pulsar de nuevo la misma valoración la retira.
  async function rateMessage(messageId: string, rating: 1 | -1) {
    const current = messages.find((m) => m.id === messageId)?.feedback ?? null;
    const next = current === rating ? null : rating;
    setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, feedback: next } : m)));
    try {
      await setMessageFeedback(messageId, next);
    } catch (err) {
      setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, feedback: current } : m)));
      if (err instanceof SessionExpiredError) {
        clearTokens();
        onSessionExpired();
      }
    }
  }

  // Aprobar (ejecuta la accion) o rechazar una accion destructiva pendiente.
  async function decideAction(messageId: string, actionId: string, decision: "approve" | "reject") {
    const patch = (update: Partial<PendingAction>) =>
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? {
                ...m,
                pending_actions: m.pending_actions?.map((a) =>
                  a.id === actionId ? { ...a, ...update } : a,
                ),
              }
            : m,
        ),
      );
    patch({ status: "approved" }); // "approved" = en curso, desactiva los botones
    try {
      const done = await decidePendingAction(actionId, decision);
      patch({ status: done.status, result: done.result });
    } catch (err) {
      if (err instanceof SessionExpiredError) {
        clearTokens();
        onSessionExpired();
        return;
      }
      patch({ status: "pending" });
      setError(err instanceof ApiError ? err.message : "No se pudo registrar tu decisión.");
    }
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter envía; Shift+Enter inserta un salto de línea.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
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
    errorRef,
    activeTool,
    handleSend,
    handleKeyDown,
    loadSession,
    startNewSession,
    stop,
    rateMessage,
    decideAction,
  };
}
