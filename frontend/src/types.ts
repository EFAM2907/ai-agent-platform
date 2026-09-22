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
