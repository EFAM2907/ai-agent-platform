import { useEffect, useState } from "react";
import {
  ApiError,
  deleteChatSession,
  getAccessToken,
  getCurrentUser,
  getGmailStatus,
  getOrganization,
  listChatSessions,
  listMembers,
  login,
  logout,
  renameChatSession,
} from "./api";
import { ChangePasswordModal, ChangePasswordScreen } from "./ChangePassword";
import { GmailPanel } from "./GmailPanel";
import { MainChat } from "./MainChat";
import { Sidebar } from "./Sidebar";
import type { ChatSessionSummary, GmailStatus, Organization, UserProfile } from "./types";
import { useChat } from "./useChat";

const PLATFORM_NAME = "AI Support";

function LoginScreen({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setIsSubmitting(true);
    setError(null);
    try {
      await login(email, password);
      onLoggedIn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo iniciar sesión");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="login-logo" />
        <h1>{PLATFORM_NAME}</h1>
        <p>Inicia sesión para continuar</p>
        <input
          type="email"
          placeholder="Correo"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
        />
        <input
          type="password"
          placeholder="Contraseña"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
        {error && <div className="login-error">{error}</div>}
        <button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Ingresando…" : "Ingresar"}
        </button>
      </form>
    </div>
  );
}

// Reemplaza al DashboardShell decorativo anterior: ahora la pagina
// principal ES el chat (sidebar + area central), con datos reales de
// la organizacion/usuario en vez de los valores fijos de antes
// ("14/20", "Pro", etc.).
function MainApp({ onLoggedOut }: { onLoggedOut: () => void }) {
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null);
  const [organization, setOrganization] = useState<Organization | null>(null);
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  // null = todavia no se sabe si puede verlos (o no tiene permiso) --
  // distinto de [] (organizacion sin mas miembros).
  const [members, setMembers] = useState<UserProfile[] | null>(null);
  const [showPasswordModal, setShowPasswordModal] = useState(false);
  // null: todavia no se sabe (o el rol no tiene permiso, GET
  // /gmail/status tambien exige ADMIN/OWNER) -- mismo criterio que
  // members. Se carga junto con members mas abajo, mismo chequeo de rol.
  const [gmailStatus, setGmailStatus] = useState<GmailStatus | null>(null);
  const [showGmailModal, setShowGmailModal] = useState(false);

  const chat = useChat(onLoggedOut);

  function handleSessionExpired() {
    onLoggedOut();
  }

  useEffect(() => {
    let cancelled = false;

    async function loadProfile() {
      let user: UserProfile;
      try {
        user = await getCurrentUser();
      } catch {
        // Sin usuario no hay nada que mostrar -- cualquier fallo acá
        // (401, backend caído, red, CORS) debe volver al login en vez
        // de dejar la app renderizada sin identidad (sidebar vacío,
        // sin nombre ni correo). A diferencia del catch de abajo, no
        // se filtra por ApiError/401: un TypeError de fetch (backend
        // todavía no levantó) nunca calificaba como sesión expirada,
        // y por eso la app se quedaba en este estado fantasma en vez
        // de mostrar el login.
        if (!cancelled) handleSessionExpired();
        return;
      }
      if (cancelled) return;
      setCurrentUser(user);

      try {
        const [org, sessionList] = await Promise.all([
          getOrganization(user.organization_id),
          listChatSessions(),
        ]);
        if (cancelled) return;
        setOrganization(org);
        setSessions(sessionList);

        // GET /users/ requiere ADMIN u OWNER -- un MEMBER/VIEWER recibe
        // 403, y eso es esperado: el sidebar simplemente no muestra la
        // seccion de Miembros para ellos (ver Sidebar.tsx, members===null).
        if (user.role === "admin" || user.role === "owner") {
          try {
            const memberList = await listMembers();
            if (!cancelled) setMembers(memberList);
          } catch {
            if (!cancelled) setMembers(null);
          }
          try {
            const status = await getGmailStatus();
            if (!cancelled) setGmailStatus(status);
          } catch {
            if (!cancelled) setGmailStatus(null);
          }
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          handleSessionExpired();
        }
      }
    }

    void loadProfile();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cuando arranca una conversacion nueva (chat.sessionId pasa de
  // null a un id real, via el evento "start" del stream), el
  // historial del sidebar todavia no la conoce -- se re-consulta acá
  // para que aparezca sin esperar a un refresh de pagina.
  useEffect(() => {
    if (!chat.sessionId) return;
    listChatSessions()
      .then((list) => setSessions(list))
      .catch(() => {
        // Best-effort: si falla, el historial se queda como estaba,
        // no vale la pena mostrarle un error al usuario por esto.
      });
  }, [chat.sessionId]);

  function handleSelectSession(id: string) {
    void chat.loadSession(id);
  }

  function handleNewChat() {
    chat.startNewSession();
  }

  // Actualizacion optimista: el sidebar ya refleja el nuevo titulo de
  // inmediato en vez de esperar un round-trip + re-listar. Si el PATCH
  // falla (sesion borrada por otra pestaña, 401, etc.) se revierte con
  // el listado real del servidor.
  async function handleRenameSession(id: string, title: string) {
    setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, title } : s)));
    try {
      await renameChatSession(id, title);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleSessionExpired();
        return;
      }
      listChatSessions()
        .then((list) => setSessions(list))
        .catch(() => {
          // Best-effort, mismo criterio que el resto de listChatSessions().
        });
    }
  }

  async function handleDeleteSession(id: string) {
    const previous = sessions;
    setSessions((prev) => prev.filter((s) => s.id !== id));
    // Si la conversacion borrada era la que estaba abierta, no tiene
    // sentido dejarla en pantalla (su sesion ya no existe en el
    // backend) -- se limpia el chat como si se hubiera pedido "Nuevo chat".
    if (chat.sessionId === id) {
      chat.startNewSession();
    }
    try {
      await deleteChatSession(id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleSessionExpired();
        return;
      }
      // Restaura el historial si el borrado en el servidor fallo --
      // mejor mostrar la conversacion "de vuelta" que dejar al usuario
      // pensando que se borro cuando en realidad sigue en la DB.
      setSessions(previous);
    }
  }

  async function handleLogout() {
    await logout();
    onLoggedOut();
  }

  // Bloquea toda la app hasta que la cuenta cambie su contraseña
  // temporal -- el backend ya rechaza /chat/messages con 403 en este
  // estado (require_password_changed), esto es lo que le explica al
  // usuario por que no puede escribir y le da como resolverlo.
  if (currentUser?.must_change_password) {
    return (
      <ChangePasswordScreen
        userName={currentUser.full_name}
        onChanged={() =>
          setCurrentUser((prev) => (prev ? { ...prev, must_change_password: false } : prev))
        }
        onLogout={() => void handleLogout()}
      />
    );
  }

  return (
    <div className="app-layout">
      <Sidebar
        platformName={PLATFORM_NAME}
        organization={organization}
        currentUser={currentUser}
        sessions={sessions}
        activeSessionId={chat.sessionId}
        members={members}
        onNewChat={handleNewChat}
        onSelectSession={handleSelectSession}
        onRenameSession={(id, title) => void handleRenameSession(id, title)}
        onDeleteSession={(id) => void handleDeleteSession(id)}
        onLogout={() => void handleLogout()}
        onChangePassword={() => setShowPasswordModal(true)}
        gmailStatus={gmailStatus}
        canManageIntegrations={currentUser?.role === "admin" || currentUser?.role === "owner"}
        onOpenGmail={() => setShowGmailModal(true)}
      />
      <MainChat
        platformName={PLATFORM_NAME}
        userName={currentUser?.full_name ?? null}
        organizationName={organization?.name ?? null}
        chat={chat}
      />
      {showPasswordModal && <ChangePasswordModal onClose={() => setShowPasswordModal(false)} />}
      {showGmailModal && (
        <GmailPanel
          status={gmailStatus}
          onStatusChange={setGmailStatus}
          onClose={() => setShowGmailModal(false)}
        />
      )}
    </div>
  );
}

export default function App() {
  const [isLoggedIn, setIsLoggedIn] = useState(() => getAccessToken() !== null);

  if (!isLoggedIn) {
    return <LoginScreen onLoggedIn={() => setIsLoggedIn(true)} />;
  }

  return <MainApp onLoggedOut={() => setIsLoggedIn(false)} />;
}
