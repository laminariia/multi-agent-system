export interface User {
  id: string;
  email: string;
  name: string | null;
  role: string;
  status: string;
  telegram_chat_id: number | null;
  created_at: string;
  last_login_at: string | null;
}

export interface RegisterPendingResponse {
  message: string;
  status: string;
}

export interface UserListResponse {
  users: User[];
  total: number;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  user: User;
}

export interface RegisterRequest {
  email: string;
  password: string;
  name?: string;
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

export interface AgentLog {
  id: string;
  timestamp: string;
  level: "info" | "warning" | "error";
  event_type: string;
  message: string | null;
  details: Record<string, any> | null;
}

export interface AgentLogList {
  agent: string;
  logs: AgentLog[];
  total: number;
}

export interface BidSummary {
  id: string;
  bid_amount: number;
  status: string;
  created_at: string;
}

export interface Job {
  id: string;
  platform: string;
  external_id: string;
  title: string;
  description: string | null;
  budget_min: number | null;
  budget_max: number | null;
  budget_type: string | null;
  currency: string;
  client_info: Record<string, any> | null;
  skills_required: string[] | null;
  deadline: string | null;
  status: string;
  score: number | null;
  disqualify_reason: string | null;
  discovered_at: string;
  url: string | null;
  bids: BidSummary[];
}

export interface JobListResponse {
  jobs: Job[];
  total: number;
}
