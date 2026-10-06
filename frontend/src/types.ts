export type MessageRole = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  sources: string[];
  created_at: string;
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
