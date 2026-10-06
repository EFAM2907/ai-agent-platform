import { useEffect, useRef, useState } from "react";
import type { ChatSessionSummary, GmailStatus, Organization, UserProfile } from "./types";
import { RobotIcon } from "./RobotIcon";

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
  // null: no se pudo consultar (rol sin permiso, o todavia no cargo) --
  // mismo criterio que members===null. canManageIntegrations es quien
  // decide si vale la pena mostrar el estado real o el texto generico.
  gmailStatus: GmailStatus | null;
  canManageIntegrations: boolean;
  onOpenGmail: () => void;
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
  canManageIntegrations,
  onOpenGmail,
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
  const menuRef = useRef<HTMLDivElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  // Cierra el menu si se hace click afuera -- sin esto, "..." se queda
  // abierto para siempre hasta el proximo click en otro item.
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

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <div className="sidebar__brand-icon">
          <RobotIcon size={18} />
        </div>
        <div className="sidebar__brand-name">{platformName}</div>
      </div>

      <button type="button" className="sidebar__new-chat" onClick={onNewChat}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
          <path d="M12 5v14M5 12h14" />
        </svg>
        Nuevo chat
      </button>

      {organization && (
        <div className="sidebar__org">
          <div className="sidebar__org-name">{organization.name}</div>
          <div className="sidebar__org-plan">Plan {organization.plan_type}</div>
        </div>
      )}

      <div className="sidebar__stats">
        <div className="sidebar__stat">
          <div className="sidebar__stat-value">{canSeeMembers ? members!.length : "—"}</div>
          <div className="sidebar__stat-label">Usuarios activos</div>
        </div>
        <div className="sidebar__stat">
          <div className="sidebar__stat-value">2</div>
          <div className="sidebar__stat-label">Integraciones</div>
        </div>
      </div>

      <div className="sidebar__section">
        <div className="sidebar__section-title">Integraciones</div>
        {/* Slack sigue siendo decorativo a proposito -- no existe todavia
            un modelo de esa integracion en el backend. Email si es real:
            Gmail esta completo (app/gmail/), asi que este ya refleja el
            estado de verdad en vez de simular data. */}
        <div className="sidebar__integration-row">
          <span className="sidebar__integration-dot sidebar__integration-dot--ok" />
          Slack
        </div>
        {canManageIntegrations ? (
          <button
            type="button"
            className="sidebar__integration-row sidebar__integration-row--btn"
            onClick={onOpenGmail}
          >
            <span
              className={`sidebar__integration-dot${gmailStatus?.connected ? " sidebar__integration-dot--ok" : ""}`}
            />
            {gmailStatus?.connected ? `Email · ${gmailStatus.google_email}` : "Email · conectar"}
          </button>
        ) : (
          <div className="sidebar__integration-row">
            <span
              className={`sidebar__integration-dot${gmailStatus?.connected ? " sidebar__integration-dot--ok" : ""}`}
            />
            Email
          </div>
        )}
      </div>

      {canSeeMembers && (
        <div className="sidebar__section">
          <button
            type="button"
            className="sidebar__section-toggle"
            onClick={() => setMembersExpanded((prev) => !prev)}
            aria-expanded={membersExpanded}
          >
            <span className="sidebar__section-title">Miembros ({members!.length})</span>
            <svg
              className={`sidebar__section-chevron${membersExpanded ? " sidebar__section-chevron--open" : ""}`}
              viewBox="0 0 24 24"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
          {membersExpanded && (
            <div className="sidebar__members">
              {members!.slice(0, 6).map((member) => (
                <div key={member.id} className="sidebar__member">
                  <span className="sidebar__member-avatar">{initials(member.full_name)}</span>
                  <span className="sidebar__member-name">{member.full_name}</span>
                  <span className="sidebar__member-role">{member.role}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="sidebar__section sidebar__section--history">
        <div className="sidebar__section-title">Historial</div>
        <div className="sidebar__history">
          {sessions.length === 0 && <div className="sidebar__history-empty">Sin conversaciones todavía</div>}
          {sessions.map((session) => {
            const isRenaming = renamingId === session.id;
            const isMenuOpen = openMenuId === session.id;
            const isConfirmingDelete = confirmingDeleteId === session.id;

            return (
              <div className="sidebar__history-row" key={session.id}>
                {isRenaming ? (
                  <input
                    ref={renameInputRef}
                    type="text"
                    className="sidebar__history-rename-input"
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
                    onClick={() => onSelectSession(session.id)}
                  >
                    {formatSessionLabel(session)}
                  </button>
                )}

                {!isRenaming && (
                  <div className="sidebar__history-menu-wrap" ref={isMenuOpen ? menuRef : undefined}>
                    <button
                      type="button"
                      className={`sidebar__history-menu-btn${isMenuOpen ? " sidebar__history-menu-btn--open" : ""}`}
                      onClick={() => toggleMenu(session.id)}
                      aria-label="Más opciones"
                    >
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
                        <circle cx="12" cy="5" r="1.8" />
                        <circle cx="12" cy="12" r="1.8" />
                        <circle cx="12" cy="19" r="1.8" />
                      </svg>
                    </button>

                    {isMenuOpen && (
                      <div className="sidebar__history-menu">
                        {isConfirmingDelete ? (
                          <>
                            <span className="sidebar__history-menu-confirm">¿Eliminar esta conversación?</span>
                            <button type="button" className="sidebar__history-menu-danger" onClick={() => confirmDelete(session.id)}>
                              Sí, eliminar
                            </button>
                            <button type="button" onClick={() => setConfirmingDeleteId(null)}>
                              Cancelar
                            </button>
                          </>
                        ) : (
                          <>
                            <button type="button" onClick={() => startRename(session)}>
                              Renombrar
                            </button>
                            <button type="button" className="sidebar__history-menu-danger" onClick={() => askDelete(session.id)}>
                              Eliminar
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {currentUser && (
        <div className="sidebar__footer">
          <span className="sidebar__member-avatar">{initials(currentUser.full_name)}</span>
          <div className="sidebar__footer-info">
            <div className="sidebar__footer-name">{currentUser.full_name}</div>
            <div className="sidebar__footer-email">{currentUser.email}</div>
          </div>
          <button
            type="button"
            className="sidebar__logout sidebar__logout--neutral"
            onClick={onChangePassword}
            aria-label="Cambiar contraseña"
            title="Cambiar contraseña"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              <rect x="5" y="11" width="14" height="9" rx="2" />
              <path d="M8 11V7a4 4 0 0 1 8 0v4" />
            </svg>
          </button>
          <button type="button" className="sidebar__logout" onClick={onLogout} aria-label="Cerrar sesión">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
            </svg>
          </button>
        </div>
      )}
    </aside>
  );
}
