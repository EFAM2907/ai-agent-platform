import { useEffect, useRef, useState } from "react";
import type { ChatSessionSummary, DriveStatus, GmailStatus, Organization, UserProfile } from "./types";
import {
  Activity,
  BookOpen,
  ChevronDown,
  ChevronsUpDown,
  KeyRound,
  LogOut,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Plug,
  Plus,
  Trash2,
} from "lucide-react";

export type AppView = "chat" | "knowledge" | "integrations" | "traces";

interface SidebarProps {
  platformName: string;
  organization: Organization | null;
  currentUser: UserProfile | null;
  sessions: ChatSessionSummary[];
  activeSessionId: string | null;
  // null: todavia no se cargaron (o el usuario no es ADMIN/OWNER y
  // GET /users/ le daria 403) -- distinto de [] (organizacion sin
  // mas miembros que el propio usuario).
  members: UserProfile[] | null;
  onNewChat: () => void;
  onSelectSession: (id: string) => void;
  onRenameSession: (id: string, title: string) => void;
  onDeleteSession: (id: string) => void;
  onLogout: () => void;
  onChangePassword: () => void;
  // Estado de las integraciones, solo para el punto de color del menu.
  // null = no se pudo consultar (rol sin permiso, o todavia no cargo).
  gmailStatus: GmailStatus | null;
  driveStatus: DriveStatus | null;
  view: AppView;
  onChangeView: (view: AppView) => void;
  // ADMIN/OWNER ven las secciones de administracion.
  isAdmin: boolean;
  // Mobile (< 768px): the sidebar is an off-canvas drawer.
  open: boolean;
  onClose: () => void;
}

function formatSessionLabel(session: ChatSessionSummary): string {
  if (session.title) return session.title;
  // La mayoria de las conversaciones todavia no tienen titulo (nada
  // en el backend lo genera hoy) -- la fecha es el fallback honesto
  // en vez de inventar un resumen que no existe.
  const date = new Date(session.created_at);
  return date.toLocaleDateString("es-CO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase();
}

const ROLE_LABELS: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Miembro",
  viewer: "Lector",
};

const HISTORY_GROUPS = ["Hoy", "Ayer", "Últimos 7 días", "Anteriores"] as const;

// Buckets a session by calendar day (local time) of its created_at.
function historyGroup(createdAt: string, now: Date): (typeof HISTORY_GROUPS)[number] {
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(new Date(createdAt))) / 86_400_000);
  if (days <= 0) return "Hoy";
  if (days === 1) return "Ayer";
  if (days <= 7) return "Últimos 7 días";
  return "Anteriores";
}

const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/i.test(navigator.platform);
const NEW_CHAT_HINT = IS_MAC ? "⌘K" : "Ctrl K";

export function Sidebar({
  platformName,
  organization,
  currentUser,
  sessions,
  activeSessionId,
  members,
  onNewChat,
  onSelectSession,
  onRenameSession,
  onDeleteSession,
  onLogout,
  onChangePassword,
  gmailStatus,
  driveStatus,
  view,
  onChangeView,
  isAdmin,
  open,
  onClose,
}: SidebarProps) {
  const canSeeMembers = members !== null;

  // Menu contextual ("...") de cada item del historial: solo uno
  // abierto a la vez, identificado por el id de la sesion.
  // Colapsado por defecto -- la lista de miembros competia por espacio
  // vertical con el historial de conversaciones, que es lo que se usa
  // todo el tiempo; el conteo solo (sin expandir) ya responde "cuantos
  // somos" sin gastar esa altura.
  const [membersExpanded, setMembersExpanded] = useState(false);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const userMenuRef = useRef<HTMLDivElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  // Cierra el menu si se hace click afuera -- sin esto, "..." se queda
  // abierto para siempre hasta el proximo click en otro item. La fila
  // que tiene el menu (o la confirmacion de borrado) abierto lleva
  // menuRef, asi que clicks dentro de ella no cuentan como "afuera".
  useEffect(() => {
    if (!openMenuId) return;
    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setOpenMenuId(null);
        setConfirmingDeleteId(null);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [openMenuId]);

  // Ctrl/Cmd + K starts a new chat from anywhere in the app.
  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        onNewChat();
        onClose();
      }
    }
    document.addEventListener("keydown", handleShortcut);
    return () => document.removeEventListener("keydown", handleShortcut);
  }, [onNewChat, onClose]);

  useEffect(() => {
    if (!userMenuOpen) return;
    function handleClickOutside(event: MouseEvent) {
      if (userMenuRef.current && !userMenuRef.current.contains(event.target as Node)) {
        setUserMenuOpen(false);
      }
    }
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setUserMenuOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [userMenuOpen]);

  // Esc closes the mobile drawer.
  useEffect(() => {
    if (!open) return;
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [open, onClose]);

  useEffect(() => {
    if (renamingId) {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    }
  }, [renamingId]);

  function toggleMenu(sessionId: string) {
    setConfirmingDeleteId(null);
    setOpenMenuId((current) => (current === sessionId ? null : sessionId));
  }

  function startRename(session: ChatSessionSummary) {
    setOpenMenuId(null);
    setRenamingId(session.id);
    setRenameValue(session.title ?? formatSessionLabel(session));
  }

  function commitRename(session: ChatSessionSummary) {
    const trimmed = renameValue.trim();
    setRenamingId(null);
    if (trimmed && trimmed !== session.title) {
      onRenameSession(session.id, trimmed);
    }
  }

  function cancelRename() {
    setRenamingId(null);
  }

  function askDelete(sessionId: string) {
    setConfirmingDeleteId(sessionId);
  }

  function confirmDelete(sessionId: string) {
    setOpenMenuId(null);
    setConfirmingDeleteId(null);
    onDeleteSession(sessionId);
  }

  const now = new Date();
  const groupedSessions = HISTORY_GROUPS.map((label) => ({
    label,
    items: sessions.filter((session) => historyGroup(session.created_at, now) === label),
  })).filter((group) => group.items.length > 0);

  function navItem(target: AppView, label: string, icon: React.ReactNode, extra?: React.ReactNode) {
    return (
      <button
        type="button"
        className={`sidebar__nav-item${view === target ? " sidebar__nav-item--active" : ""}`}
        aria-current={view === target ? "page" : undefined}
        onClick={() => {
          onChangeView(target);
          onClose();
        }}
      >
        {icon}
        <span>{label}</span>
        {extra}
      </button>
    );
  }

  return (
    <>
      <div
        className={`sidebar-backdrop${open ? " sidebar-backdrop--open" : ""}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside className={`sidebar${open ? " sidebar--open" : ""}`} aria-label="Navegación principal">
        <div className="sidebar__brand">
          <span className="logo-mark" aria-hidden="true">
            A
          </span>
          <div className="sidebar__brand-name">{platformName}</div>
        </div>

        {organization && (
          <div className="sidebar__org" title={organization.name}>
            <span className="sidebar__org-initials" aria-hidden="true">
              {initials(organization.name) || "?"}
            </span>
            <span className="sidebar__org-name">{organization.name}</span>
            <span className="badge badge--accent">{organization.plan_type}</span>
            <ChevronsUpDown size={16} strokeWidth={1.75} aria-hidden="true" />
          </div>
        )}

        <button
          type="button"
          className="sidebar__new-chat"
          onClick={() => {
            onNewChat();
            onClose();
          }}
        >
          <Plus size={16} strokeWidth={1.75} />
          <span>Nuevo chat</span>
          <kbd>{NEW_CHAT_HINT}</kbd>
        </button>

        <nav className="sidebar__nav" aria-label="Secciones">
          {navItem("chat", "Chat", <MessageSquare size={16} strokeWidth={1.75} />)}
          {isAdmin && (
            <>
              {navItem("knowledge", "Base de conocimiento", <BookOpen size={16} strokeWidth={1.75} />)}
              {navItem(
                "integrations",
                "Integraciones",
                <Plug size={16} strokeWidth={1.75} />,
                <span className="sidebar__nav-dots" aria-hidden="true">
                  <span
                    className={`sidebar__integration-dot${gmailStatus?.connected ? " sidebar__integration-dot--ok" : ""}`}
                    title={gmailStatus?.connected ? "Gmail conectado" : "Gmail sin conectar"}
                  />
                  <span
                    className={`sidebar__integration-dot${driveStatus?.connected ? " sidebar__integration-dot--ok" : ""}`}
                    title={driveStatus?.connected ? "Drive conectado" : "Drive sin conectar"}
                  />
                </span>,
              )}
              {navItem("traces", "Trazabilidad", <Activity size={16} strokeWidth={1.75} />)}
            </>
          )}
        </nav>

        {canSeeMembers && (
          <div className="sidebar__section">
            <button
              type="button"
              className="sidebar__section-toggle"
              onClick={() => setMembersExpanded((prev) => !prev)}
              aria-expanded={membersExpanded}
            >
              <span className="sidebar__section-title">
                Miembros <span className="mono">({members!.length})</span>
              </span>
              <ChevronDown
                size={14}
                strokeWidth={1.75}
                className={`sidebar__section-chevron${membersExpanded ? " sidebar__section-chevron--open" : ""}`}
              />
            </button>
            {membersExpanded && (
              <div className="sidebar__members">
                {members!.slice(0, 6).map((member) => (
                  <div key={member.id} className="sidebar__member">
                    <span className="avatar">{initials(member.full_name)}</span>
                    <span className="sidebar__member-name">{member.full_name}</span>
                    <span className="badge">{ROLE_LABELS[member.role] ?? member.role}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="sidebar__section sidebar__section--history">
          <div className="sidebar__history">
            {sessions.length === 0 && <div className="sidebar__history-empty">Sin conversaciones todavía</div>}
            {groupedSessions.map((group) => (
              <div key={group.label} role="group" aria-label={group.label}>
                <div className="sidebar__history-label">{group.label}</div>
                {group.items.map((session) => {
                  const isRenaming = renamingId === session.id;
                  const isMenuOpen = openMenuId === session.id;
                  const isConfirmingDelete = confirmingDeleteId === session.id;

                  return (
                    <div
                      className="sidebar__history-row"
                      key={session.id}
                      ref={isMenuOpen || isConfirmingDelete ? menuRef : undefined}
                    >
                      {isConfirmingDelete ? (
                        <div className="sidebar__history-confirm" role="alert">
                          <span>¿Eliminar?</span>
                          <button type="button" onClick={() => confirmDelete(session.id)}>
                            Eliminar
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setConfirmingDeleteId(null);
                              setOpenMenuId(null);
                            }}
                          >
                            Cancelar
                          </button>
                        </div>
                      ) : isRenaming ? (
                        <input
                          ref={renameInputRef}
                          type="text"
                          className="sidebar__history-rename-input"
                          aria-label="Nuevo nombre de la conversación"
                          value={renameValue}
                          maxLength={200}
                          onChange={(event) => setRenameValue(event.target.value)}
                          onBlur={() => commitRename(session)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              event.preventDefault();
                              commitRename(session);
                            } else if (event.key === "Escape") {
                              event.preventDefault();
                              cancelRename();
                            }
                          }}
                        />
                      ) : (
                        <button
                          type="button"
                          title={session.title ?? undefined}
                          className={`sidebar__history-item${session.id === activeSessionId ? " sidebar__history-item--active" : ""}`}
                          aria-current={session.id === activeSessionId ? "true" : undefined}
                          onClick={() => {
                            onSelectSession(session.id);
                            onClose();
                          }}
                        >
                          {formatSessionLabel(session)}
                        </button>
                      )}

                      {!isRenaming && !isConfirmingDelete && (
                        <div className="sidebar__history-menu-wrap">
                          <button
                            type="button"
                            className={`sidebar__history-menu-btn${isMenuOpen ? " sidebar__history-menu-btn--open" : ""}`}
                            onClick={() => toggleMenu(session.id)}
                            aria-label="Más opciones"
                            aria-expanded={isMenuOpen}
                          >
                            <MoreHorizontal size={16} strokeWidth={1.75} />
                          </button>

                          {isMenuOpen && (
                            <div className="sidebar__history-menu">
                              <button type="button" onClick={() => startRename(session)}>
                                <Pencil size={14} strokeWidth={1.75} />
                                Renombrar
                              </button>
                              <button
                                type="button"
                                className="sidebar__history-menu-danger"
                                onClick={() => askDelete(session.id)}
                              >
                                <Trash2 size={14} strokeWidth={1.75} />
                                Eliminar
                              </button>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>

        {currentUser && (
          <div className="sidebar__footer-wrap" ref={userMenuRef}>
            {userMenuOpen && (
              <div className="sidebar__user-menu" role="menu">
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setUserMenuOpen(false);
                    onChangePassword();
                  }}
                >
                  <KeyRound size={16} strokeWidth={1.75} />
                  Cambiar contraseña
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setUserMenuOpen(false);
                    onLogout();
                  }}
                >
                  <LogOut size={16} strokeWidth={1.75} />
                  Cerrar sesión
                </button>
              </div>
            )}
            <button
              type="button"
              className="sidebar__footer"
              aria-haspopup="menu"
              aria-expanded={userMenuOpen}
              onClick={() => setUserMenuOpen((prev) => !prev)}
            >
              <span className="avatar">{initials(currentUser.full_name)}</span>
              <div className="sidebar__footer-info">
                <div className="sidebar__footer-name">{currentUser.full_name}</div>
                <div className="sidebar__footer-email">{currentUser.email}</div>
              </div>
              <span className="badge">{ROLE_LABELS[currentUser.role] ?? currentUser.role}</span>
            </button>
          </div>
        )}
      </aside>
    </>
  );
}
