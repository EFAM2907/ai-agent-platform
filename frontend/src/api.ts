import type {
  ChatReply,
  ChatSession,
  ChatSessionSummary,
  GmailPollResult,
  GmailStatus,
  GmailUnresolvedList,
  Organization,
  UserProfile,
} from "./types";

// Sin barra final a proposito, mismo motivo que settings.litellm_base_url
// en el backend (app/core/config.py): evita el bug clasico de URL con
// doble "//" si alguien deja la barra en el .env.
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

const ACCESS_TOKEN_KEY = "access_token";
const REFRESH_TOKEN_KEY = "refresh_token";
// La conversacion activa tambien vive en localStorage (ver useChat.ts),
// sin ningun scope por usuario -- si no se limpia junto con los
// tokens, un segundo usuario que inicia sesion en el mismo navegador
// hereda el session_id del anterior, y GET /chat/sessions/{id} se lo
// resuelve igual (esta bien: es su propia conversacion, existe de
// verdad) mostrando el chat de otra persona. Definido aca, no en
// useChat.ts, para que sea un solo lugar el que sepa que claves de
// localStorage pertenecen a "la sesion actual".
export const SESSION_STORAGE_KEY = "chat_session_id";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// Se lanza cuando el refresh token tambien fallo (revocado/expirado) --
// distinta de ApiError para que la UI sepa que debe volver al login en
// vez de mostrar el error inline de siempre.
export class SessionExpiredError extends ApiError {}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

function setTokens(accessToken: string, refreshToken: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(SESSION_STORAGE_KEY);
}

async function parseErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return body?.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

// Deduplicado a una sola promesa en curso: si dos requests reciben 401
// al mismo tiempo, no queremos que cada una llame a /auth/refresh por
// su cuenta -- el backend revoca el refresh token usado en cada llamada
// (AuthService.refresh), asi que la segunda llamada fallaria con un
// refresh token ya revocado por la primera.
let refreshInFlight: Promise<string> | null = null;

async function refreshAccessToken(): Promise<string> {
  if (refreshInFlight) return refreshInFlight;

  const storedRefreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
  if (!storedRefreshToken) {
    throw new SessionExpiredError(401, "No hay sesión activa");
  }

  refreshInFlight = (async () => {
    const response = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: storedRefreshToken }),
    });
    if (!response.ok) {
      clearTokens();
      throw new SessionExpiredError(response.status, "Tu sesión expiró. Inicia sesión de nuevo.");
    }
    const tokens = (await response.json()) as { access_token: string; refresh_token: string };
    setTokens(tokens.access_token, tokens.refresh_token);
    return tokens.access_token;
  })();

  try {
    return await refreshInFlight;
  } finally {
    refreshInFlight = null;
  }
}

async function authedRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const accessToken = getAccessToken();
  if (!accessToken) {
    throw new SessionExpiredError(401, "No hay sesión activa");
  }

  const doFetch = (token: string) =>
    fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...(init?.headers ?? {}),
      },
    });

  let response = await doFetch(accessToken);

  if (response.status === 401) {
    const newAccessToken = await refreshAccessToken();
    response = await doFetch(newAccessToken);
  }

  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorDetail(response));
  }
  // DELETE /chat/sessions/{id} responde 204 sin body -- .json() sobre
  // una respuesta vacia lanza un SyntaxError, asi que se corta antes.
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export async function login(email: string, password: string): Promise<void> {
  const response = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorDetail(response));
  }
  const tokens = (await response.json()) as { access_token: string; refresh_token: string };
  setTokens(tokens.access_token, tokens.refresh_token);
  // Un login exitoso es una identidad nueva en este navegador -- nunca
  // debe arrancar mostrando la conversacion de quien haya usado esta
  // sesion antes (ver el comentario de SESSION_STORAGE_KEY arriba).
  localStorage.removeItem(SESSION_STORAGE_KEY);
}

export async function logout(): Promise<void> {
  const storedRefreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
  clearTokens();
  if (!storedRefreshToken) return;

  // Best-effort: si falla (red caida, token ya vencido) el usuario
  // igual queda deslogueado localmente, que es lo que le importa.
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: storedRefreshToken }),
    });
  } catch {
    // ignorado a proposito, ver comentario arriba
  }
}

export async function sendChatMessage(
  message: string,
  sessionId: string | null,
): Promise<ChatReply> {
  return authedRequest<ChatReply>("/chat/messages", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, message }),
  });
}

export type ChatStreamEvent =
  | { type: "start"; data: { session_id: string; sources: string[] } }
  | { type: "delta"; data: { content: string } }
  | { type: "done"; data: { message_id: string; created_at: string } }
  | { type: "error"; data: { detail: string } };

// No usa EventSource nativo a proposito: EventSource solo hace GET y no
// permite mandar el header Authorization, y esta API es Bearer-token,
// no de cookies -- por eso este parser manual sobre fetch() + un
// ReadableStream, con el mismo patron de refresh-en-401 que
// authedRequest (que no se puede reusar tal cual porque authedRequest
// espera .json() al final, no un stream).
async function doStreamRequest(token: string, body: unknown): Promise<Response> {
  return fetch(`${API_BASE}/chat/messages/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });
}

export async function* streamChatMessage(
  message: string,
  sessionId: string | null,
): AsyncGenerator<ChatStreamEvent> {
  const accessToken = getAccessToken();
  if (!accessToken) {
    throw new SessionExpiredError(401, "No hay sesión activa");
  }

  const body = { session_id: sessionId, message };
  let response = await doStreamRequest(accessToken, body);

  if (response.status === 401) {
    const newAccessToken = await refreshAccessToken();
    response = await doStreamRequest(newAccessToken, body);
  }

  if (!response.ok || !response.body) {
    throw new ApiError(response.status, await parseErrorDetail(response));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Cada evento SSE termina en una linea en blanco ("\n\n"). Puede
    // llegar mas de uno por chunk de red, o uno partido a la mitad --
    // por eso se acumula en buffer y solo se procesa lo que ya tiene
    // el separador completo.
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);

      const lines = rawEvent.split("\n");
      const eventLine = lines.find((line) => line.startsWith("event:"));
      const dataLine = lines.find((line) => line.startsWith("data:"));
      if (eventLine && dataLine) {
        yield {
          type: eventLine.slice(6).trim(),
          data: JSON.parse(dataLine.slice(5).trim()),
        } as ChatStreamEvent;
      }

      boundary = buffer.indexOf("\n\n");
    }
  }
}

export async function getChatSession(sessionId: string): Promise<ChatSession> {
  return authedRequest<ChatSession>(`/chat/sessions/${sessionId}`);
}

export async function listChatSessions(): Promise<ChatSessionSummary[]> {
  return authedRequest<ChatSessionSummary[]>("/chat/sessions");
}

export async function renameChatSession(id: string, title: string): Promise<ChatSessionSummary> {
  return authedRequest<ChatSessionSummary>(`/chat/sessions/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export async function deleteChatSession(id: string): Promise<void> {
  await authedRequest<void>(`/chat/sessions/${id}`, { method: "DELETE" });
}

export async function listMembers(): Promise<UserProfile[]> {
  return authedRequest<UserProfile[]>("/users/?limit=100");
}

export async function getCurrentUser(): Promise<UserProfile> {
  return authedRequest<UserProfile>("/users/me");
}

// No requiere que must_change_password ya este en false (ver
// app.auth.api.change_password): es la unica ruta de negocio que se
// puede llamar mientras esa bandera sigue en True, a proposito, o la
// cuenta nunca podria salir de ese estado.
export async function changePassword(newPassword: string): Promise<void> {
  await authedRequest<void>("/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ new_password: newPassword }),
  });
}

export async function getOrganization(organizationId: string): Promise<Organization> {
  return authedRequest<Organization>(`/organizations/${organizationId}`);
}

// -- Gmail --
// Requieren ADMIN/OWNER en el backend (ver app/gmail/api.py) -- solo se
// llaman cuando App.tsx ya confirmo ese rol, igual que listMembers().

export async function getGmailStatus(): Promise<GmailStatus> {
  return authedRequest<GmailStatus>("/gmail/status");
}

export async function getGmailAuthorizationUrl(): Promise<{ authorization_url: string }> {
  return authedRequest<{ authorization_url: string }>("/gmail/connect");
}

export async function disconnectGmail(): Promise<void> {
  await authedRequest<void>("/gmail/connection", { method: "DELETE" });
}

export async function pollGmailNow(): Promise<GmailPollResult> {
  return authedRequest<GmailPollResult>("/gmail/poll", { method: "POST" });
}

export async function listUnresolvedGmail(): Promise<GmailUnresolvedList> {
  return authedRequest<GmailUnresolvedList>("/gmail/unresolved?limit=20");
}
