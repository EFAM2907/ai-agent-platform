import { AlertCircle, ExternalLink, HardDrive, Loader2, Mail } from "lucide-react";
import { useEffect, useState } from "react";
import {
  ApiError,
  disconnectDrive,
  disconnectGmail,
  getDriveAuthorizationUrl,
  getDriveStatus,
  getGmailAuthorizationUrl,
  getGmailStatus,
  listUnresolvedGmail,
  pollGmailNow,
  previewDriveSync,
  setDriveFolder,
  syncDrive,
} from "./api";
import { PageShell } from "./PageShell";
import type {
  DriveFileOutcome,
  DrivePreview,
  DriveStatus,
  DriveSyncSummary,
  GmailPollResult,
  GmailStatus,
  GmailUnresolvedMessage,
} from "./types";

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("es-CO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function StatusIndicator({ on }: { on: boolean }) {
  return (
    <span className="integration__status">
      <span className={`status-dot${on ? " status-dot--ok" : ""}`} aria-hidden="true" />
      {on ? "Conectado" : "Sin conectar"}
    </span>
  );
}

function ErrorNotice({ message }: { message: string }) {
  return (
    <div className="notice notice--error" role="alert">
      <AlertCircle size={16} strokeWidth={1.75} />
      {message}
    </div>
  );
}

function Pill({ label, value, tone }: { label: string; value: number; tone?: "ok" | "bad" }) {
  return (
    <span className={`pill${tone ? ` pill--${tone}` : ""}`}>
      {label} <b>{value}</b>
    </span>
  );
}

function BusyLabel({ busy, label }: { busy: boolean; label: string }) {
  return busy ? (
    <>
      <Loader2 size={14} strokeWidth={1.75} className="spin" />
      {label}
    </>
  ) : null;
}

// ---------------------------------------------------------------------
// Gmail
// ---------------------------------------------------------------------
function GmailCard({
  status,
  onStatusChange,
}: {
  status: GmailStatus | null;
  onStatusChange: (status: GmailStatus) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pollResult, setPollResult] = useState<GmailPollResult | null>(null);
  const [unresolved, setUnresolved] = useState<GmailUnresolvedMessage[] | null>(null);
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);

  useEffect(() => {
    if (!status?.connected) return;
    listUnresolvedGmail()
      .then((list) => setUnresolved(list.messages))
      .catch(() => undefined); // informativo: no bloquea la tarjeta
  }, [status?.connected]);

  async function run<T>(name: string, action: () => Promise<T>, failure: string): Promise<T | undefined> {
    setError(null);
    setBusy(name);
    try {
      return await action();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : failure);
      return undefined;
    } finally {
      setBusy(null);
    }
  }

  async function connect() {
    const result = await run("connect", getGmailAuthorizationUrl, "No se pudo iniciar la conexión con Gmail");
    // Nueva pestaña: el callback del backend termina en su propia página.
    if (result) window.open(result.authorization_url, "_blank", "noopener,noreferrer");
  }

  async function refresh() {
    const fresh = await run("refresh", getGmailStatus, "No se pudo actualizar el estado");
    if (fresh) onStatusChange(fresh);
  }

  async function poll() {
    setPollResult(null);
    const result = await run("poll", pollGmailNow, "No se pudo revisar el buzón");
    if (!result) return;
    setPollResult(result);
    const [fresh, list] = await Promise.all([getGmailStatus(), listUnresolvedGmail()]);
    onStatusChange(fresh);
    setUnresolved(list.messages);
  }

  async function disconnect() {
    setError(null);
    setBusy("disconnect");
    try {
      await disconnectGmail();
      onStatusChange({ connected: false });
      setConfirmingDisconnect(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo desconectar Gmail");
    } finally {
      setBusy(null);
    }
  }

  const connected = !!status?.connected;

  return (
    <section className="card integration">
      <div className="integration__head">
        <span className="integration__icon" aria-hidden="true">
          <Mail size={16} strokeWidth={1.75} />
        </span>
        <h2 className="integration__name">Gmail</h2>
        <StatusIndicator on={connected} />
      </div>
      {connected && status?.google_email && <div className="integration__account">{status.google_email}</div>}
      <p className="integration__desc">
        El asistente responde por correo las consultas de seguimiento de guías (solo si quien escribe es el cliente del
        envío) y envía las invitaciones de usuarios nuevos.
      </p>
      {error && <ErrorNotice message={error} />}

      {!connected ? (
        <div className="row">
          <button type="button" className="btn btn--primary" onClick={() => void connect()} disabled={busy !== null}>
            <BusyLabel busy={busy === "connect"} label="Abriendo Google…" />
            {busy !== "connect" && "Conectar Gmail"}
          </button>
          <button type="button" className="btn" onClick={() => void refresh()} disabled={busy !== null}>
            <BusyLabel busy={busy === "refresh"} label="Actualizando…" />
            {busy !== "refresh" && "Ya autoricé, actualizar"}
          </button>
        </div>
      ) : (
        <>
          <dl className="meta">
            <dt>Conectado</dt>
            <dd className="mono">{formatDate(status.connected_at)}</dd>
            <dt>Última revisión</dt>
            <dd className="mono">{formatDate(status.last_polled_at)}</dd>
          </dl>
          {pollResult && (
            <div className="pills" role="status" aria-label="Resultado de la revisión">
              <Pill label="examinados" value={pollResult.examined} />
              <Pill label="respondidos" value={pollResult.replied} tone="ok" />
              <Pill label="omitidos" value={pollResult.skipped} />
              <Pill label="fallidos" value={pollResult.failed} tone={pollResult.failed > 0 ? "bad" : undefined} />
            </div>
          )}
          <div className="row">
            <button type="button" className="btn btn--primary" onClick={() => void poll()} disabled={busy !== null}>
              <BusyLabel busy={busy === "poll"} label="Revisando…" />
              {busy !== "poll" && "Revisar ahora"}
            </button>
            {!confirmingDisconnect && (
              <button type="button" className="btn btn--ghost" onClick={() => setConfirmingDisconnect(true)}>
                Desconectar
              </button>
            )}
          </div>
          {confirmingDisconnect && (
            <div className="confirm-row" role="alert">
              <span className="confirm-row__label">¿Desconectar esta cuenta?</span>
              <button type="button" className="btn btn--danger btn--sm" onClick={() => void disconnect()} disabled={busy !== null}>
                Sí, desconectar
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setConfirmingDisconnect(false)}>
                Cancelar
              </button>
            </div>
          )}

          {unresolved && unresolved.length > 0 && (
            <div className="subsection">
              <h3>
                Correos sin resolver <span className="mono">({unresolved.length})</span>
              </h3>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Remitente</th>
                      <th>Detalle</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {unresolved.slice(0, 8).map((message) => (
                      <tr key={message.id}>
                        <td className="table__title">{message.sender ?? "remitente desconocido"}</td>
                        <td className="table__title muted">{message.detail ?? message.outcome}</td>
                        <td className="table__actions">
                          {message.gmail_link && (
                            <a className="link" href={message.gmail_link} target="_blank" rel="noreferrer">
                              Ver <ExternalLink size={12} strokeWidth={1.75} aria-hidden="true" />
                            </a>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------
// Google Drive
// ---------------------------------------------------------------------
const OUTCOME_LABELS: Record<DriveFileOutcome["status"], string> = {
  new: "nuevo",
  changed: "modificado",
  unchanged: "sin cambios",
  unsupported: "no soportado",
  failed: "con error",
};

function OutcomeTable({ files }: { files: DriveFileOutcome[] }) {
  if (files.length === 0) return <p className="muted">La carpeta no tiene archivos.</p>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Archivo</th>
            <th>Estado</th>
            <th>Detalle</th>
          </tr>
        </thead>
        <tbody>
          {files.map((file, index) => (
            <tr key={`${file.path}${file.name}-${index}`}>
              <td className="table__title">
                {file.path}
                {file.name}
              </td>
              <td>
                <span
                  className={`badge${file.status === "failed" ? " badge--bad" : file.status === "new" || file.status === "changed" ? " badge--ok" : ""}`}
                >
                  {OUTCOME_LABELS[file.status]}
                </span>
              </td>
              <td className="table__title muted">{file.reason ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DriveCard({
  status,
  onStatusChange,
}: {
  status: DriveStatus | null;
  onStatusChange: (status: DriveStatus) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [folderInput, setFolderInput] = useState("");
  const [editingFolder, setEditingFolder] = useState(false);
  const [preview, setPreview] = useState<DrivePreview | null>(null);
  const [syncResult, setSyncResult] = useState<DriveSyncSummary | null>(null);
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);
  const [removeDocs, setRemoveDocs] = useState(false);

  async function run<T>(name: string, action: () => Promise<T>, failure: string): Promise<T | undefined> {
    setError(null);
    setBusy(name);
    try {
      return await action();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : failure);
      return undefined;
    } finally {
      setBusy(null);
    }
  }

  async function connect() {
    const result = await run("connect", getDriveAuthorizationUrl, "No se pudo iniciar la conexión con Drive");
    if (result) window.open(result.authorization_url, "_blank", "noopener,noreferrer");
  }

  async function refresh() {
    const fresh = await run("refresh", getDriveStatus, "No se pudo actualizar el estado");
    if (fresh) onStatusChange(fresh);
  }

  async function saveFolder(event: React.FormEvent) {
    event.preventDefault();
    const updated = await run("folder", () => setDriveFolder(folderInput), "No se pudo elegir la carpeta");
    if (updated) {
      onStatusChange(updated);
      setEditingFolder(false);
      setFolderInput("");
      setPreview(null);
      setSyncResult(null);
    }
  }

  async function doPreview() {
    setSyncResult(null);
    const result = await run("preview", previewDriveSync, "No se pudo consultar la carpeta");
    if (result) setPreview(result);
  }

  async function doSync() {
    setPreview(null);
    const result = await run("sync", syncDrive, "No se pudo sincronizar");
    if (result) {
      setSyncResult(result);
      onStatusChange(await getDriveStatus());
    } else {
      // Un 503 por cuota deja indexado lo ya procesado: se refresca el estado igual.
      getDriveStatus().then(onStatusChange).catch(() => undefined);
    }
  }

  async function disconnect() {
    setError(null);
    setBusy("disconnect");
    try {
      await disconnectDrive(removeDocs);
      onStatusChange({ connected: false });
      setConfirmingDisconnect(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo desconectar Drive");
    } finally {
      setBusy(null);
    }
  }

  const connected = !!status?.connected;
  const hasFolder = connected && !!status?.folder_id;
  const last = status?.last_sync;

  return (
    <section className="card integration">
      <div className="integration__head">
        <span className="integration__icon" aria-hidden="true">
          <HardDrive size={16} strokeWidth={1.75} />
        </span>
        <h2 className="integration__name">Google Drive</h2>
        <StatusIndicator on={connected} />
      </div>
      {connected && status?.google_email && <div className="integration__account">{status.google_email}</div>}
      <p className="integration__desc">
        Sincroniza una carpeta de Drive con la base de conocimiento: documentos de Google, Word, Excel, PDF y Markdown.
        Solo lectura — la aplicación nunca modifica tus archivos. Lo que cambia se actualiza solo y lo que borras de la
        carpeta se retira del asistente.
      </p>
      {error && <ErrorNotice message={error} />}

      {!connected ? (
        <div className="row">
          <button type="button" className="btn btn--primary" onClick={() => void connect()} disabled={busy !== null}>
            <BusyLabel busy={busy === "connect"} label="Abriendo Google…" />
            {busy !== "connect" && "Conectar Google Drive"}
          </button>
          <button type="button" className="btn" onClick={() => void refresh()} disabled={busy !== null}>
            <BusyLabel busy={busy === "refresh"} label="Actualizando…" />
            {busy !== "refresh" && "Ya autoricé, actualizar"}
          </button>
        </div>
      ) : (
        <>
          {(!hasFolder || editingFolder) && (
            <form className="inline-form" onSubmit={(event) => void saveFolder(event)}>
              <input
                type="text"
                placeholder="Pega el enlace de la carpeta de Drive (o su id)"
                aria-label="Enlace o id de la carpeta de Drive"
                value={folderInput}
                onChange={(event) => setFolderInput(event.target.value)}
                required
              />
              <button type="submit" className="btn btn--primary" disabled={busy !== null || !folderInput.trim()}>
                <BusyLabel busy={busy === "folder"} label="Verificando…" />
                {busy !== "folder" && "Usar esta carpeta"}
              </button>
              {editingFolder && (
                <button type="button" className="btn btn--ghost" onClick={() => setEditingFolder(false)}>
                  Cancelar
                </button>
              )}
            </form>
          )}

          {hasFolder && !editingFolder && (
            <>
              <dl className="meta">
                <dt>Carpeta</dt>
                <dd>
                  {status?.folder_name}{" "}
                  <button type="button" className="link" onClick={() => setEditingFolder(true)}>
                    cambiar
                  </button>
                </dd>
                <dt>Última sincronización</dt>
                <dd className="mono">{formatDate(status?.last_synced_at)}</dd>
              </dl>
              {last && (
                <div className="pills" aria-label="Resultado de la última sincronización">
                  <Pill label="indexados" value={last.documents} tone="ok" />
                  <Pill label="sin cambios" value={last.unchanged} />
                  <Pill label="retirados" value={last.pruned} />
                  {last.failed.length > 0 && <Pill label="con error" value={last.failed.length} tone="bad" />}
                </div>
              )}
              <div className="row">
                <button type="button" className="btn btn--primary" onClick={() => void doSync()} disabled={busy !== null}>
                  <BusyLabel busy={busy === "sync"} label="Sincronizando…" />
                  {busy !== "sync" && "Sincronizar ahora"}
                </button>
                <button type="button" className="btn" onClick={() => void doPreview()} disabled={busy !== null}>
                  <BusyLabel busy={busy === "preview"} label="Consultando…" />
                  {busy !== "preview" && "Ver qué cambiaría"}
                </button>
              </div>
            </>
          )}

          {preview && (
            <div className="subsection">
              <div className="pills" aria-label="Vista previa de la sincronización">
                <Pill label="nuevos" value={preview.new} tone="ok" />
                <Pill label="modificados" value={preview.changed} tone="ok" />
                <Pill label="sin cambios" value={preview.unchanged} />
                <Pill label="no soportados" value={preview.unsupported} />
              </div>
              {preview.truncated && (
                <p className="muted">La carpeta tiene demasiados archivos; se muestran los primeros 500 y no se retira nada.</p>
              )}
              <OutcomeTable files={preview.files} />
            </div>
          )}

          {syncResult && (
            <div className="subsection">
              <div className="pills" role="status" aria-label="Resultado de la sincronización">
                <Pill label="indexados" value={syncResult.documents} tone="ok" />
                <Pill label="sin cambios" value={syncResult.unchanged} />
                <Pill label="retirados" value={syncResult.pruned} />
                {syncResult.failed.length > 0 && <Pill label="con error" value={syncResult.failed.length} tone="bad" />}
              </div>
              {(syncResult.failed.length > 0 || syncResult.unsupported.length > 0) && (
                <OutcomeTable files={[...syncResult.failed, ...syncResult.unsupported]} />
              )}
            </div>
          )}

          {confirmingDisconnect ? (
            <div className="confirm-row" role="alert">
              <label className="field-inline">
                <input type="checkbox" checked={removeDocs} onChange={(event) => setRemoveDocs(event.target.checked)} />
                También quitar los documentos de Drive del asistente
              </label>
              <span className="confirm-row__label" />
              <button type="button" className="btn btn--danger btn--sm" onClick={() => void disconnect()} disabled={busy !== null}>
                Sí, desconectar
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setConfirmingDisconnect(false)}>
                Cancelar
              </button>
            </div>
          ) : (
            <div className="row row--end">
              <button type="button" className="btn btn--ghost" onClick={() => setConfirmingDisconnect(true)}>
                Desconectar
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

export function IntegrationsView({
  gmailStatus,
  onGmailStatusChange,
  driveStatus,
  onDriveStatusChange,
}: {
  gmailStatus: GmailStatus | null;
  onGmailStatusChange: (status: GmailStatus) => void;
  driveStatus: DriveStatus | null;
  onDriveStatusChange: (status: DriveStatus) => void;
}) {
  return (
    <PageShell
      title="Integraciones"
      description="Conecta las cuentas de tu organización. Cada integración es de tu organización y no se comparte con otras."
    >
      <div className="integration-grid">
        <GmailCard status={gmailStatus} onStatusChange={onGmailStatusChange} />
        <DriveCard status={driveStatus} onStatusChange={onDriveStatusChange} />
      </div>
    </PageShell>
  );
}
