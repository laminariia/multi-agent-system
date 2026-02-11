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
  type: "bid_approval" | "code_review" | "delivery" | "revision" | "scope_creep" | "plan_review" | "alert" | "email_approval" | "final_review" | "job_review";
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
  by_type: Record<string, { pending: number; resolved: number }>;
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

// --- Pipeline B ---

export interface Lead {
  id: string;
  name: string;
  category: string | null;
  city: string | null;
  address: string | null;
  phone: string | null;
  email: string | null;
  status: string;
  enrichment_source: string | null;
  discovered_at: string | null;
}

export interface LeadListResponse {
  leads: Lead[];
  total: number;
}

export interface PipelineBStats {
  total_leads: number;
  by_status: Record<string, number>;
  top_cities: { city: string; count: number }[];
}

export interface ScanResponse {
  status: string;
  thread_id: string;
  city: string;
  message: string;
}

// --- Orchestrator ---

export interface OrchestratorStatus {
  alive: boolean;
  pid: number | null;
  uptime_seconds: number | null;
  mode: string | null;
  goals_pending: number;
  goals_completed: number;
  goals_failed: number;
  health_grade: string | null;
  health_score: number | null;
}

export interface Goal {
  id: string;
  title: string;
  priority: string;
  category: string;
  status: string;
  completed_at: string | null;
  result: string | null;
}

export interface GoalListResponse {
  goals: Goal[];
  total: number;
  pending: number;
  completed: number;
  failed: number;
}

export interface HealthDimension {
  grade: string;
  notes: string | null;
}

export interface HealthProblem {
  severity: string;
  description: string;
}

export interface HealthReport {
  overall_grade: string;
  score: number;
  dimensions: Record<string, HealthDimension>;
  problems: HealthProblem[];
}

export interface Phase {
  number: number;
  title: string;
  is_future: boolean;
  milestones: Milestone[];
}

export interface Milestone {
  text: string;
  done: boolean;
}

export interface LogLine {
  line: string;
  level: "INFO" | "WARN" | "ERROR" | string;
  timestamp: string | null;
}

export interface LogResponse {
  lines: LogLine[];
  total: number;
  log_file: string | null;
}
