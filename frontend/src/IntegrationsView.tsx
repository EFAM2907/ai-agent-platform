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

function Dot({ on }: { on: boolean }) {
  return <span className={`status-dot${on ? " status-dot--ok" : ""}`} aria-hidden="true" />;
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

  return (
    <section className="card integration">
      <div className="card__head">
        <h2>
          <Dot on={!!status?.connected} /> Gmail
        </h2>
        {status?.connected && <span className="muted">{status.google_email}</span>}
      </div>
      <p className="muted">
        El asistente responde por correo las consultas de seguimiento de guías (solo si quien escribe es el cliente del
        envío) y envía las invitaciones de usuarios nuevos.
      </p>
      {error && <div className="login-error">{error}</div>}

      {!status?.connected ? (
        <div className="row">
          <button type="button" onClick={() => void connect()} disabled={busy !== null}>
            {busy === "connect" ? "Abriendo Google…" : "Conectar Gmail"}
          </button>
          <button type="button" className="login-secondary" onClick={() => void refresh()} disabled={busy !== null}>
            {busy === "refresh" ? "Actualizando…" : "Ya autoricé, actualizar"}
          </button>
        </div>
      ) : (
        <>
          <dl className="meta">
            <dt>Conectado</dt>
            <dd>{formatDate(status.connected_at)}</dd>
            <dt>Última revisión</dt>
            <dd>{formatDate(status.last_polled_at)}</dd>
          </dl>
          {pollResult && (
            <div className="password-success">
              Revisados {pollResult.examined}, respondidos {pollResult.replied}, sin resolver{" "}
              {pollResult.skipped + pollResult.failed}.
            </div>
          )}
          <div className="row">
            <button type="button" onClick={() => void poll()} disabled={busy !== null}>
              {busy === "poll" ? "Revisando…" : "Revisar ahora"}
            </button>
            {confirmingDisconnect ? (
              <>
                <span className="muted">¿Desconectar esta cuenta?</span>
                <button type="button" className="link-danger" onClick={() => void disconnect()} disabled={busy !== null}>
                  Sí, desconectar
                </button>
                <button type="button" className="link" onClick={() => setConfirmingDisconnect(false)}>
                  Cancelar
                </button>
              </>
            ) : (
              <button type="button" className="login-secondary" onClick={() => setConfirmingDisconnect(true)}>
                Desconectar
              </button>
            )}
          </div>

          {unresolved && unresolved.length > 0 && (
            <div className="subsection">
              <h3>Correos sin resolver ({unresolved.length})</h3>
              <ul className="plain-list">
                {unresolved.slice(0, 8).map((message) => (
                  <li key={message.id}>
                    <strong>{message.sender ?? "remitente desconocido"}</strong>
                    <span className="muted"> — {message.detail ?? message.outcome}</span>
                    {message.gmail_link && (
                      <>
                        {" "}
                        <a href={message.gmail_link} target="_blank" rel="noreferrer">
                          Ver
                        </a>
                      </>
                    )}
                  </li>
                ))}
              </ul>
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
                <span className={`chip${file.status === "failed" ? " chip--bad" : file.status === "new" || file.status === "changed" ? " chip--ok" : ""}`}>
                  {OUTCOME_LABELS[file.status]}
                </span>
              </td>
              <td className="muted">{file.reason ?? ""}</td>
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
      <div className="card__head">
        <h2>
          <Dot on={connected} /> Google Drive
        </h2>
        {connected && <span className="muted">{status?.google_email}</span>}
      </div>
      <p className="muted">
        Sincroniza una carpeta de Drive con la base de conocimiento: documentos de Google, Word, Excel, PDF y Markdown.
        Solo lectura — la aplicación nunca modifica tus archivos. Lo que cambia se actualiza solo y lo que borras de la
        carpeta se retira del asistente.
      </p>
      {error && <div className="login-error">{error}</div>}

      {!connected ? (
        <div className="row">
          <button type="button" onClick={() => void connect()} disabled={busy !== null}>
            {busy === "connect" ? "Abriendo Google…" : "Conectar Google Drive"}
          </button>
          <button type="button" className="login-secondary" onClick={() => void refresh()} disabled={busy !== null}>
            {busy === "refresh" ? "Actualizando…" : "Ya autoricé, actualizar"}
          </button>
        </div>
      ) : (
        <>
          {(!hasFolder || editingFolder) && (
            <form className="inline-form" onSubmit={(event) => void saveFolder(event)}>
              <input
                type="text"
                placeholder="Pega el enlace de la carpeta de Drive (o su id)"
                value={folderInput}
                onChange={(event) => setFolderInput(event.target.value)}
                required
              />
              <button type="submit" disabled={busy !== null || !folderInput.trim()}>
                {busy === "folder" ? "Verificando…" : "Usar esta carpeta"}
              </button>
              {editingFolder && (
                <button type="button" className="login-secondary" onClick={() => setEditingFolder(false)}>
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
                <dd>{formatDate(status?.last_synced_at)}</dd>
                {last && (
                  <>
                    <dt>Resultado</dt>
                    <dd>
                      {last.documents} indexados · {last.unchanged} sin cambios · {last.pruned} retirados
                      {last.failed.length > 0 && ` · ${last.failed.length} con error`}
                    </dd>
                  </>
                )}
              </dl>
              <div className="row">
                <button type="button" onClick={() => void doSync()} disabled={busy !== null}>
                  {busy === "sync" ? "Sincronizando…" : "Sincronizar ahora"}
                </button>
                <button type="button" className="login-secondary" onClick={() => void doPreview()} disabled={busy !== null}>
                  {busy === "preview" ? "Consultando…" : "Ver qué cambiaría"}
                </button>
              </div>
            </>
          )}

          {preview && (
            <div className="subsection">
              <h3>
                {preview.new} nuevos · {preview.changed} modificados · {preview.unchanged} sin cambios ·{" "}
                {preview.unsupported} no soportados
              </h3>
              {preview.truncated && (
                <p className="muted">La carpeta tiene demasiados archivos; se muestran los primeros 500 y no se retira nada.</p>
              )}
              <OutcomeTable files={preview.files} />
            </div>
          )}

          {syncResult && (
            <div className="subsection">
              <div className="password-success">
                Sincronizado: {syncResult.documents} indexados, {syncResult.unchanged} sin cambios,{" "}
                {syncResult.pruned} retirados.
              </div>
              {(syncResult.failed.length > 0 || syncResult.unsupported.length > 0) && (
                <OutcomeTable files={[...syncResult.failed, ...syncResult.unsupported]} />
              )}
            </div>
          )}

          <div className="row row--end">
            {confirmingDisconnect ? (
              <>
                <label className="field-inline">
                  <input type="checkbox" checked={removeDocs} onChange={(event) => setRemoveDocs(event.target.checked)} />
                  También quitar los documentos de Drive del asistente
                </label>
                <button type="button" className="link-danger" onClick={() => void disconnect()} disabled={busy !== null}>
                  Sí, desconectar
                </button>
                <button type="button" className="link" onClick={() => setConfirmingDisconnect(false)}>
                  Cancelar
                </button>
              </>
            ) : (
              <button type="button" className="login-secondary" onClick={() => setConfirmingDisconnect(true)}>
                Desconectar
              </button>
            )}
          </div>
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
    <div className="view">
      <header className="view__header">
        <h1>Integraciones</h1>
        <p>Conecta las cuentas de tu organización. Cada integración es de tu organización y no se comparte con otras.</p>
      </header>
      <GmailCard status={gmailStatus} onStatusChange={onGmailStatusChange} />
      <DriveCard status={driveStatus} onStatusChange={onDriveStatusChange} />
    </div>
  );
}
