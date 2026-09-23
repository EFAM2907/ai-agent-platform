import { useEffect, useState } from "react";
import {
  ApiError,
  disconnectGmail,
  getGmailAuthorizationUrl,
  getGmailStatus,
  listUnresolvedGmail,
  pollGmailNow,
} from "./api";
import type { GmailPollResult, GmailStatus, GmailUnresolvedMessage } from "./types";

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("es-CO", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface GmailPanelProps {
  status: GmailStatus | null;
  onStatusChange: (status: GmailStatus) => void;
  onClose: () => void;
}

// Modal voluntario para conectar/gestionar Gmail desde el sidebar --
// mismo look que ChangePasswordModal (.modal-overlay/.modal-card).
// No hay poller en docker-compose (decision explicita: pesaria una
// imagen nueva completa y esto no es visible en una demo) -- por eso
// el boton "Revisar ahora" existe: deja probar la integracion sin
// depender de un proceso en background corriendo aparte.
export function GmailPanel({ status, onStatusChange, onClose }: GmailPanelProps) {
  const [connecting, setConnecting] = useState(false);
  const [refreshingStatus, setRefreshingStatus] = useState(false);
  const [polling, setPolling] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollResult, setPollResult] = useState<GmailPollResult | null>(null);
  const [unresolved, setUnresolved] = useState<GmailUnresolvedMessage[] | null>(null);

  useEffect(() => {
    if (!status?.connected) return;
    listUnresolvedGmail()
      .then((list) => setUnresolved(list.messages))
      .catch(() => {
        // Best-effort -- es informativo, no vale la pena bloquear el
        // panel si falla (mismo criterio que listMembers en App.tsx).
      });
  }, [status?.connected]);

  async function handleConnect() {
    setError(null);
    setConnecting(true);
    try {
      const { authorization_url } = await getGmailAuthorizationUrl();
      // Nueva pestaña, no redirige la app entera: el callback del
      // backend (GET /gmail/callback) termina en una pagina propia
      // que le pide al usuario cerrarla, nunca vuelve al frontend.
      window.open(authorization_url, "_blank", "noopener,noreferrer");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo iniciar la conexión con Gmail");
    } finally {
      setConnecting(false);
    }
  }

  async function handleRefreshStatus() {
    setError(null);
    setRefreshingStatus(true);
    try {
      onStatusChange(await getGmailStatus());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo actualizar el estado");
    } finally {
      setRefreshingStatus(false);
    }
  }

  async function handlePollNow() {
    setError(null);
    setPolling(true);
    setPollResult(null);
    try {
      const result = await pollGmailNow();
      setPollResult(result);
      const [freshStatus, freshUnresolved] = await Promise.all([
        getGmailStatus(),
        listUnresolvedGmail(),
      ]);
      onStatusChange(freshStatus);
      setUnresolved(freshUnresolved.messages);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo revisar el buzón");
    } finally {
      setPolling(false);
    }
  }

  async function handleDisconnect() {
    setError(null);
    setDisconnecting(true);
    try {
      await disconnectGmail();
      setConfirmingDisconnect(false);
      onStatusChange({ connected: false });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo desconectar Gmail");
    } finally {
      setDisconnecting(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card gmail-panel" onClick={(event) => event.stopPropagation()}>
        <h2>Gmail</h2>

        {!status?.connected ? (
          <>
            <p className="gmail-panel__desc">
              Conecta una cuenta de Gmail para que el agente responda solo preguntas de
              seguimiento que lleguen por correo, y para enviar el correo de bienvenida
              cuando se invita a un usuario desde el chat.
            </p>
            {error && <div className="login-error">{error}</div>}
            <div className="modal-actions">
              <button type="button" className="login-secondary" onClick={onClose}>
                Cerrar
              </button>
              <button type="button" onClick={() => void handleConnect()} disabled={connecting}>
                {connecting ? "Abriendo Google…" : "Conectar Gmail"}
              </button>
            </div>
            <button
              type="button"
              className="gmail-panel__refresh"
              onClick={() => void handleRefreshStatus()}
              disabled={refreshingStatus}
            >
              {refreshingStatus ? "Actualizando…" : "Ya autoricé, actualizar estado"}
            </button>
          </>
        ) : (
          <>
            <div className="gmail-panel__account">
              <span className="sidebar__integration-dot sidebar__integration-dot--ok" />
              {status.google_email}
            </div>
            <dl className="gmail-panel__meta">
              <dt>Conectado</dt>
              <dd>{formatDate(status.connected_at)}</dd>
              <dt>Última revisión</dt>
              <dd>{formatDate(status.last_polled_at)}</dd>
            </dl>

            {error && <div className="login-error">{error}</div>}

            {pollResult && (
              <div className="password-success">
                Revisados {pollResult.examined}, respondidos {pollResult.replied}, sin
                resolver {pollResult.skipped + pollResult.failed}.
              </div>
            )}

            <div className="modal-actions">
              <button type="button" className="login-secondary" onClick={onClose}>
                Cerrar
              </button>
              <button type="button" onClick={() => void handlePollNow()} disabled={polling}>
                {polling ? "Revisando…" : "Revisar ahora"}
              </button>
            </div>

            {unresolved && unresolved.length > 0 && (
              <div className="gmail-panel__unresolved">
                <div className="sidebar__section-title">Sin resolver ({unresolved.length})</div>
                {unresolved.slice(0, 5).map((message) => (
                  <div key={message.id} className="gmail-panel__unresolved-row">
                    <span className="gmail-panel__unresolved-sender">
                      {message.sender ?? "remitente desconocido"}
                    </span>
                    <span className="gmail-panel__unresolved-detail">
                      {message.detail ?? message.outcome}
                    </span>
                    {message.gmail_link && (
                      <a href={message.gmail_link} target="_blank" rel="noreferrer">
                        Ver
                      </a>
                    )}
                  </div>
                ))}
              </div>
            )}

            <div className="gmail-panel__disconnect">
              {confirmingDisconnect ? (
                <>
                  <span className="sidebar__history-menu-confirm">
                    ¿Desconectar esta cuenta de Gmail?
                  </span>
                  <button
                    type="button"
                    className="sidebar__history-menu-danger"
                    onClick={() => void handleDisconnect()}
                    disabled={disconnecting}
                  >
                    {disconnecting ? "Desconectando…" : "Sí, desconectar"}
                  </button>
                  <button
                    type="button"
                    className="login-secondary"
                    onClick={() => setConfirmingDisconnect(false)}
                  >
                    Cancelar
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="login-secondary"
                  onClick={() => setConfirmingDisconnect(true)}
                >
                  Desconectar Gmail
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
