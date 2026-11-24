import { AlertCircle, Check, Loader2, Trash2, UploadCloud, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  deleteKbDocument,
  getKbStats,
  listKbDocuments,
  searchKb,
  updateKbDocumentAccess,
  uploadKbDocument,
} from "./api";
import { PageShell } from "./PageShell";
import type { KBDocument, KBSearchResponse, KBStats, KBUploadResult } from "./types";

const SOURCE_LABELS: Record<string, string> = {
  file: "Carpeta",
  upload: "Subido",
  drive: "Google Drive",
  manual: "Manual",
};

const ACCESS_LABELS: Record<string, string> = {
  viewer: "Todos",
  member: "Miembros y superiores",
  admin: "Solo administradores",
  owner: "Solo propietario",
};

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("es-CO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

const STATUS_TEXT: Record<KBUploadResult["status"], string> = {
  created: "nuevo",
  updated: "actualizado",
  unchanged: "sin cambios",
};

interface UploadLine {
  filename: string;
  state: "uploading" | "done" | "error";
  results?: KBUploadResult[];
  error?: string;
}

function ErrorNotice({ message }: { message: string }) {
  return (
    <div className="notice notice--error" role="alert">
      <AlertCircle size={16} strokeWidth={1.75} />
      {message}
    </div>
  );
}

function UploadZone({ formats, onUploaded }: { formats: string[]; onUploaded: () => void }) {
  const [accessLevel, setAccessLevel] = useState("viewer");
  const [dragging, setDragging] = useState(false);
  const [lines, setLines] = useState<UploadLine[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  async function uploadFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (list.length === 0) return;
    setLines((prev) => [...list.map((f) => ({ filename: f.name, state: "uploading" as const })), ...prev]);

    // De a uno: el servidor embebe cada documento y el proveedor limita las
    // peticiones por minuto; en paralelo se agota la cuota más rápido.
    for (const file of list) {
      try {
        const response = await uploadKbDocument(file, accessLevel);
        setLines((prev) =>
          prev.map((line) =>
            line.filename === file.name && line.state === "uploading"
              ? { filename: file.name, state: "done", results: response.documents }
              : line,
          ),
        );
      } catch (err) {
        setLines((prev) =>
          prev.map((line) =>
            line.filename === file.name && line.state === "uploading"
              ? { filename: file.name, state: "error", error: err instanceof ApiError ? err.message : "No se pudo subir" }
              : line,
          ),
        );
      }
    }
    onUploaded();
  }

  return (
    <section className="card">
      <div className="card__head">
        <h2>Subir documentos</h2>
        <label className="field-inline">
          Visible para
          <select value={accessLevel} onChange={(event) => setAccessLevel(event.target.value)}>
            {Object.entries(ACCESS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div
        className={`dropzone${dragging ? " dropzone--active" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void uploadFiles(event.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
        }}
      >
        <UploadCloud size={20} strokeWidth={1.75} aria-hidden="true" />
        <strong>Arrastra archivos aquí o haz clic para elegirlos</strong>
        <span>Formatos: {formats.join(", ") || "…"} · máximo 10 MB por archivo</span>
        <input
          ref={inputRef}
          type="file"
          multiple
          hidden
          accept={formats.join(",")}
          onChange={(event) => {
            if (event.target.files) void uploadFiles(event.target.files);
            event.target.value = "";
          }}
        />
      </div>
      {lines.length > 0 && (
        <ul className="upload-list">
          {lines.slice(0, 8).map((line, index) => (
            <li key={`${line.filename}-${index}`}>
              <span className="upload-list__name">{line.filename}</span>
              {line.state === "uploading" && (
                <span className="upload-list__state muted">
                  <Loader2 size={14} strokeWidth={1.75} className="spin" />
                  Indexando…
                </span>
              )}
              {line.state === "error" && (
                <span className="upload-list__state text-bad">
                  <X size={14} strokeWidth={1.75} />
                  {line.error}
                </span>
              )}
              {line.state === "done" &&
                line.results?.map((result) => (
                  <span key={result.id} className="badge badge--ok">
                    <Check size={12} strokeWidth={1.75} aria-hidden="true" />
                    &nbsp;{result.title} · {STATUS_TEXT[result.status]} ·&nbsp;
                    <span className="mono">{result.chunk_count}</span>&nbsp;fragmentos
                  </span>
                ))}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SearchTester() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<KBSearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(event: React.FormEvent) {
    event.preventDefault();
    if (query.trim().length < 2) return;
    setLoading(true);
    setError(null);
    try {
      setResponse(await searchKb(query.trim(), 5));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo buscar");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="card">
      <div className="card__head">
        <h2>Probar una búsqueda</h2>
        <span className="muted">Lo mismo que ve el asistente, según tu rol</span>
      </div>
      <form className="inline-form" onSubmit={(event) => void run(event)}>
        <input
          type="text"
          placeholder="Ej.: tarifa Bogotá a Leticia, código N-07, horario OFC-MED-003…"
          aria-label="Consulta de búsqueda"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button type="submit" className="btn btn--primary" disabled={loading || query.trim().length < 2}>
          {loading ? (
            <>
              <Loader2 size={14} strokeWidth={1.75} className="spin" />
              Buscando…
            </>
          ) : (
            "Buscar"
          )}
        </button>
      </form>
      {error && <ErrorNotice message={error} />}
      {response && response.results.length === 0 && (
        <p className="muted">
          Nada relevante: el asistente respondería que no tiene esa información (o usaría una herramienta).
        </p>
      )}
      {response && response.results.length > 0 && (
        <ol className="hits">
          {response.results.map((hit) => (
            <li key={`${hit.article_id}-${hit.chunk_index}`} className="hit">
              <div className="hit__head">
                <span className="hit__title">{hit.title}</span>
                <span className="score-bar" role="img" aria-label={`Relevancia ${hit.score.toFixed(3)}`}>
                  <span className="score-bar__fill" style={{ width: `${Math.max(0, Math.min(1, hit.score)) * 100}%` }} />
                </span>
                <span className="hit__score mono">{hit.score.toFixed(3)}</span>
              </div>
              <p className="hit__snippet">{hit.snippet}</p>
              <span className="hit__meta">
                Fragmento <span className="mono">{hit.chunk_index + 1}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function KnowledgeView() {
  const [stats, setStats] = useState<KBStats | null>(null);
  const [documents, setDocuments] = useState<KBDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [source, setSource] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [statsData, list] = await Promise.all([getKbStats(), listKbDocuments({ source: source || undefined, limit: 100 })]);
      setStats(statsData);
      setDocuments(list.documents);
      setTotal(list.total);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo cargar la base de conocimiento");
    } finally {
      setLoading(false);
    }
  }, [source]);

  useEffect(() => {
    void load();
  }, [load]);

  async function changeAccess(doc: KBDocument, level: string) {
    const previous = documents;
    setDocuments((prev) => prev.map((d) => (d.id === doc.id ? { ...d, access_level: level as KBDocument["access_level"] } : d)));
    try {
      await updateKbDocumentAccess(doc.id, level);
    } catch {
      setDocuments(previous);
      setError("No se pudo cambiar el acceso.");
    }
  }

  async function remove(doc: KBDocument) {
    setConfirmingId(null);
    try {
      await deleteKbDocument(doc.id);
      await load();
    } catch {
      setError("No se pudo eliminar el documento.");
    }
  }

  return (
    <PageShell
      title="Base de conocimiento"
      description="Lo que el asistente consulta para responder sobre políticas, tarifas, oficinas y procedimientos."
    >
      {error && <ErrorNotice message={error} />}

      <div className="stat-cards">
        <div className="stat-card">
          <div className="stat-card__value">{stats?.articles ?? "—"}</div>
          <div className="stat-card__label">Documentos</div>
        </div>
        <div className="stat-card">
          <div className="stat-card__value">{stats?.chunks ?? "—"}</div>
          <div className="stat-card__label">Fragmentos indexados</div>
        </div>
        {stats &&
          Object.entries(stats.by_source).map(([key, count]) => (
            <div className="stat-card" key={key}>
              <div className="stat-card__value">{count}</div>
              <div className="stat-card__label">{SOURCE_LABELS[key] ?? key}</div>
            </div>
          ))}
      </div>

      <UploadZone formats={stats?.supported_formats ?? []} onUploaded={() => void load()} />
      <SearchTester />

      <section className="card">
        <div className="card__head">
          <h2>
            Documentos <span className="mono">({total})</span>
          </h2>
          <label className="field-inline">
            Origen
            <select value={source} onChange={(event) => setSource(event.target.value)}>
              <option value="">Todos</option>
              {Object.entries(SOURCE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {loading ? (
          <p className="muted">Cargando…</p>
        ) : documents.length === 0 ? (
          <p className="muted">Todavía no hay documentos. Sube archivos o conecta Google Drive en Integraciones.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Título</th>
                  <th>Origen</th>
                  <th className="table__num">Fragmentos</th>
                  <th>Visible para</th>
                  <th>Indexado</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <tr key={doc.id}>
                    <td className="table__title" title={doc.source_ref ?? undefined}>
                      {doc.title}
                    </td>
                    <td>
                      <span className="badge">{SOURCE_LABELS[doc.source_type] ?? doc.source_type}</span>
                    </td>
                    <td className="mono table__num">{doc.chunk_count}</td>
                    <td>
                      <select
                        className="select-small"
                        value={doc.access_level}
                        onChange={(event) => void changeAccess(doc, event.target.value)}
                        aria-label={`Quién puede leer ${doc.title}`}
                      >
                        {Object.entries(ACCESS_LABELS).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="mono muted">{formatDate(doc.indexed_at)}</td>
                    <td className="table__actions">
                      {confirmingId === doc.id ? (
                        <>
                          <button type="button" className="btn btn--danger btn--sm" onClick={() => void remove(doc)}>
                            Sí, eliminar
                          </button>{" "}
                          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setConfirmingId(null)}>
                            Cancelar
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          onClick={() => setConfirmingId(doc.id)}
                          aria-label={`Eliminar ${doc.title}`}
                        >
                          <Trash2 size={14} strokeWidth={1.75} />
                          Eliminar
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </PageShell>
  );
}
