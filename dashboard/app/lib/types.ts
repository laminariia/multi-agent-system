export interface User {
  id: string;
  email: string;
  name: string | null;
  role: string;
  telegram_chat_id: number | null;
  created_at: string;
  last_login_at: string | null;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  user: User;
}

export interface HITLItem {
  id: string;
  type: "bid_approval" | "code_review" | "delivery" | "revision" | "alert";
  priority: "urgent" | "normal" | "low";
  title: string;
  description: string | null;
  expires_at: string | null;
  payload: Record<string, any>;
  available_actions: string[];
  created_at: string;
}

export interface HITLPendingResponse {
  items: HITLItem[];
  total: number;
  pending_urgent: number;
}

export interface HITLResolveResponse {
  id: string;
  status: string;
  resolution: string;
  next_action: string;
}

export interface HITLStats {
  today: {
    pending: number;
    resolved: number;
    expired: number;
  };
  avg_resolution_time_minutes: number;
  by_type: Record<string, number>;
}

export interface AgentStatus {
  name: string;
  display_name: string | null;
  pipeline: string | null;
  status: "idle" | "working" | "error" | "dead" | "paused";
  last_heartbeat: string | null;
  current_task: string | null;
  restart_count: number;
  error_message: string | null;
}

export interface AgentStatusList {
  system_health: string;
  last_check: string;
  agents: AgentStatus[];
}
