export type MessageRole = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  sources: string[];
  created_at: string;
  // 1 (util), -1 (no util) o null/undefined: valoracion del usuario.
  feedback?: number | null;
  // Solo en respuestas recien generadas en esta sesion de la pagina; las que
  // vienen del historial se piden con GET /chat/messages/{id}/trace.
  trace?: TraceSummaryLite | null;
  // Acciones destructivas que el agente pidio en este turno y esperan tu
  // aprobacion (no se ejecutaron). Estado solo de esta pantalla.
  pending_actions?: PendingAction[];
}

export type PendingActionStatus =
  | "pending"
  | "approved"
  | "executed"
  | "failed"
  | "rejected"
  | "expired";

export interface PendingAction {
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  expires_at: string;
  status: PendingActionStatus;
  result?: unknown;
}

export interface ChatReply {
  session_id: string;
  message: ChatMessage;
}

export interface ChatSession {
  id: string;
  title: string | null;
  created_at: string;
  messages: ChatMessage[];
}

export interface ChatSessionSummary {
  id: string;
  title: string | null;
  created_at: string;
}

export interface UserProfile {
  id: string;
  email: string;
  full_name: string;
  role: string;
  organization_id: string;
  created_at: string;
  must_change_password: boolean;
}

export interface Organization {
  id: string;
  name: string;
  tax_id: string | null;
  plan_type: string;
  created_at: string;
}

export interface GmailStatus {
  connected: boolean;
  google_email?: string | null;
  connected_at?: string | null;
  last_polled_at?: string | null;
}

export interface GmailPollResult {
  examined: number;
  replied: number;
  skipped: number;
  failed: number;
  skip_reasons: Record<string, number>;
}

export interface GmailUnresolvedMessage {
  id: string;
  sender: string | null;
  tracking_number: string | null;
  outcome: string;
  detail: string | null;
  attempts: number;
  processed_at: string;
  gmail_link: string | null;
}

export interface GmailUnresolvedList {
  total: number;
  returned: number;
  messages: GmailUnresolvedMessage[];
}

// ---------------------------------------------------------------------
// Chat: feedback, traza y estado de herramientas
// ---------------------------------------------------------------------
export interface TraceSummaryLite {
  trace_id: string;
  model: string | null;
  latency_ms: number;
  tools: { name: string; ok: boolean }[];
}

export interface TraceChunk {
  chunk_id: string;
  article_id: string;
  title: string;
  score: number;
}

export interface TraceToolCall {
  name: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  duration_ms: number;
  result_chars: number;
  error?: string | null;
}

export interface TraceLLMCall {
  purpose: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  cost_usd: number | null;
  finish_reason: string;
}

export interface TraceDetail {
  id: string;
  trace_id: string;
  created_at: string;
  user_id: string;
  session_id: string;
  user_message_id: string | null;
  assistant_message_id: string | null;
  status: "ok" | "error";
  error_type: string | null;
  error_message: string | null;
  model: string | null;
  route: { model?: string; reason?: string };
  retrieval: {
    mode?: string;
    returned?: number;
    latency_ms?: number;
    error?: string;
    chunks?: TraceChunk[];
  };
  llm_calls: TraceLLMCall[];
  tool_calls: TraceToolCall[];
  delegation?: { actor?: string; sub?: string; jti?: string; aud?: string; scopes?: string[] };
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  latency_ms: number;
}

export interface TraceSummary {
  id: string;
  trace_id: string;
  created_at: string;
  user_id: string;
  session_id: string;
  status: "ok" | "error";
  error_type: string | null;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  latency_ms: number;
  tools_used: number;
  kb_chunks: number;
  route_reason: string | null;
}

export interface TraceList {
  total: number;
  returned: number;
  traces: TraceSummary[];
}

export interface TraceStats {
  days: number;
  turns: number;
  errors: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  by_model: { name: string; count: number }[];
  by_tool: { name: string; count: number; failed: number }[];
  feedback_positive: number;
  feedback_negative: number;
}

// ---------------------------------------------------------------------
// Base de conocimiento
// ---------------------------------------------------------------------
export interface KBDocument {
  id: string;
  title: string;
  source_type: "file" | "upload" | "drive" | "manual";
  source_ref: string | null;
  mime_type: string | null;
  access_level: "viewer" | "member" | "admin" | "owner";
  chunk_count: number;
  indexed_at: string | null;
  source_modified_at: string | null;
  updated_at: string;
}

export interface KBDocumentList {
  total: number;
  returned: number;
  documents: KBDocument[];
}

export interface KBStats {
  articles: number;
  chunks: number;
  by_source: Record<string, number>;
  supported_formats: string[];
}

export interface KBUploadResult {
  id: string;
  title: string;
  chunk_count: number;
  status: "created" | "updated" | "unchanged";
}

export interface KBUploadResponse {
  filename: string;
  documents: KBUploadResult[];
}

export interface KBSearchHit {
  article_id: string;
  title: string;
  chunk_index: number;
  score: number;
  snippet: string;
}

export interface KBSearchResponse {
  query: string;
  results: KBSearchHit[];
}

// ---------------------------------------------------------------------
// Google Drive
// ---------------------------------------------------------------------
export interface DriveFileOutcome {
  name: string;
  path: string;
  status: "new" | "changed" | "unchanged" | "unsupported" | "failed";
  reason?: string | null;
}

export interface DriveSyncSummary {
  documents: number;
  unchanged: number;
  pruned: number;
  truncated: boolean;
  unsupported: DriveFileOutcome[];
  failed: DriveFileOutcome[];
  at?: string | null;
}

export interface DriveStatus {
  connected: boolean;
  google_email?: string | null;
  connected_at?: string | null;
  folder_id?: string | null;
  folder_name?: string | null;
  last_synced_at?: string | null;
  last_sync?: DriveSyncSummary | null;
}

export interface DrivePreview {
  truncated: boolean;
  new: number;
  changed: number;
  unchanged: number;
  unsupported: number;
  files: DriveFileOutcome[];
}
