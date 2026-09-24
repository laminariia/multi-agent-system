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
  type:
    | "bid_approval"
    | "code_review"
    | "delivery"
    | "revision"
    | "scope_creep"
    | "plan_review"
    | "alert"
    | "email_approval"
    | "final_review"
    | "job_review"
    | "dev_launch"
    | "agent_failure"
    | "delivery_hold"
    | "outreach_approval"
    | "manual_action"
    | "concept_review";
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

export interface HITLBulkResolveResponse {
  resolved: number;
  failed: number;
  errors: { id: string; error: string }[];
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

export interface HITLTrends {
  days: number;
  trends: { date: string; created: number; resolved: number }[];
  totals: { created: number; resolved: number; pending: number };
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
  uptime_seconds?: number;
  llm_tokens_used?: number;
  avg_task_duration?: number;
  success_rate?: number;
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

export interface JobStats {
  total: number;
  by_platform: { platform: string; count: number }[];
  by_status: Record<string, number>;
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
  latitude: number | null;
  longitude: number | null;
  lead_score: number | null;
}

export interface LeadDetail extends Lead {
  country: string | null;
  latitude: number | null;
  longitude: number | null;
  h3_index: string | null;
  website: string | null;
  social_links: Record<string, string> | null;
  enrichment_cost: number | null;
  enrichment_data: Record<string, any> | null;
  osm_id: string | null;
  temperature: string | null;
  google_rating: number | null;
  review_count: number | null;
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

export interface JobScanResponse {
  status: string;
  platform: string;
  message: string;
}

export interface RunPipelineResponse {
  status: string;
  job_id: string;
  thread_id: string;
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
  cpu_percent?: number;
  memory_percent?: number;
  db_connections?: number;
  db_max_connections?: number;
  valkey_latency_ms?: number;
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

// --- Settings ---

export interface PlatformAccount {
  id: string;
  platform: string;
  username: string | null;
  status: string;
  profile_url: string | null;
  stats: Record<string, any>;
  last_health_check: string | null;
  credentials_masked: Record<string, string>;
  created_at: string | null;
  updated_at: string | null;
}

export interface APIKeyStatus {
  configured: boolean;
  source: string | null;
  masked: string | null;
}

export interface CredentialTestResult {
  key_name: string;
  success: boolean;
  message: string;
  latency_ms: number | null;
}

export interface CredentialsSummary {
  api_keys: Record<string, APIKeyStatus>;
  platform_accounts: PlatformAccount[];
}

// --- Scout Config ---

export interface ScoutConfig {
  categories_auto: string[];
  categories_suggest: string[];
  custom_rules: string[];
  geo_categories: string[];
  prospect_rules: string[];
}

// --- Campaigns ---

export interface EmailCampaign {
  id: number;
  name: string;
  status: string;
  subject: string;
  body: string;
  city_filter: string | null;
  category_filter: string | null;
  leads_count: number;
  sent_count: number;
  opened_count: number;
  replied_count: number;
  bounced_count: number;
  created_at: string;
}

export interface CampaignLead {
  id: number;
  lead_id: number;
  business_name: string;
  email: string;
  status: string;
  sent_at: string | null;
  opened_at: string | null;
}

export interface CampaignListResponse {
  campaigns: EmailCampaign[];
  total: number;
}

export interface CampaignLeadListResponse {
  leads: CampaignLead[];
  total: number;
}

export interface CreateCampaignPayload {
  name: string;
  subject: string;
  body: string;
  city_filter?: string;
  category_filter?: string;
}

export interface TelegramChannel {
  id: number;
  username: string;
  title: string | null;
  category: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface TelegramChannelListResponse {
  channels: TelegramChannel[];
  total: number;
}

// --- Deals (Pipeline B → A bridge) ---

export interface Deal {
  id: string;
  lead_id: string | null;
  title: string;
  status: string;
  agreed_scope: string | null;
  budget: number | null;
  deadline: string | null;
  pipeline_a_thread_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface DealDetail extends Deal {
  client_context: Record<string, any> | null;
  design_versions: Record<string, any>[] | null;
  conversation_history: Record<string, any>[] | null;
}

export interface DealListResponse {
  deals: Deal[];
  total: number;
}

export interface PipelineProgress {
  thread_id: string | null;
  agent_sequence: string[];
  current_agent: string | null;
  current_index: number;
  total_agents: number;
  completed_agents: string[];
  status: "idle" | "running" | "paused" | "completed" | "failed";
  started_at: string | null;
  updated_at: string | null;
}

// --- Bids ---

export interface Bid {
  id: string;
  job_id: string;
  job_title: string | null;
  platform: string;
  bid_amount: number;
  delivery_days: number | null;
  cover_letter: string | null;
  status: "draft" | "submitted" | "accepted" | "rejected" | "withdrawn";
  platform_bid_id: string | null;
  submitted_at: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface BidListResponse {
  bids: Bid[];
  total: number;
}

export interface BidStats {
  total: number;
  by_status: Record<string, number>;
  avg_bid_amount: number | null;
  win_rate: number | null;
}

// --- Projects ---

export interface Project {
  id: string;
  job_id: string | null;
  deal_id: string | null;
  title: string;
  status: "active" | "completed" | "on_hold" | "cancelled";
  client_name: string | null;
  budget: number | null;
  deadline: string | null;
  description: string | null;
  artifacts: Record<string, any> | null;
  progress: number | null;
  created_at: string;
  updated_at: string | null;
}

export interface ProjectListResponse {
  projects: Project[];
  total: number;
}

// --- Portfolio ---

export interface PortfolioProject {
  id: string;
  title: string;
  description: string | null;
  category: string | null;
  tags: string[] | null;
  tech_stack: string[] | null;
  platform: string | null;
  image_url: string | null;
  demo_url: string | null;
  source_url: string | null;
  url: string | null;
  client_name: string | null;
  budget: number | null;
  completed_at: string | null;
  visible: boolean;
  created_at: string;
}

export interface PortfolioListResponse {
  projects: PortfolioProject[];
  total: number;
  published_count: number;
  draft_count: number;
}

// --- Analytics ---

export interface FunnelStep {
  label: string;
  count: number;
  rate: number | null;
}

export interface CostBreakdown {
  category: string;
  amount: number;
}

export interface RevenueByPlatform {
  platform: string;
  revenue: number;
  deals: number;
}

export interface LLMCostByAgent {
  agent: string;
  cost_usd: number;
  calls: number;
}

export interface AnalyticsOverview {
  period_days: number;
  jobs_discovered: number;
  bids_submitted: number;
  deals_won: number;
  revenue_total: number;
  avg_deal_value: number | null;
  pipeline_a_funnel: FunnelStep[];
  pipeline_b_funnel: FunnelStep[];
  cost_breakdown: CostBreakdown[];
  revenue_by_platform: RevenueByPlatform[];
  llm_cost_by_agent: LLMCostByAgent[];
}

export interface AnalyticsData {
  overview: AnalyticsOverview;
  generated_at: string;
}

// --- Health Metrics (Orchestrator real data) ---

export interface HealthMetrics {
  cpu_percent: number;
  memory_percent: number;
  db_connections: number;
  db_max_connections: number;
  valkey_latency_ms: number;
}

// --- Agent Performance ---

export interface AgentPerformance {
  agent_name: string;
  weekly_tasks: { day: string; tasks: number }[];
  uptime_percent: number;
  llm_tokens_used: number;
  avg_task_duration_seconds: number;
  success_rate: number;
}

// --- Bid Chat / Negotiation ---

export type NegotiationStatus =
  | "initial"
  | "qualifying"
  | "proposing"
  | "negotiating"
  | "closing";

export interface BidMessage {
  id: string;
  sender: "ai" | "client" | "operator";
  text: string;
  timestamp: string;
}

export interface BidChat {
  bid_id: string;
  messages: BidMessage[];
  negotiation_status: NegotiationStatus;
}

// --- Bid Detail / Negotiation ---

export type NegotiationStage =
  | "initial"
  | "qualifying"
  | "proposing"
  | "negotiating"
  | "closing"
  | "won"
  | "lost";

export interface BidDetail extends Bid {
  proposal_text: string | null;
  confidence_score: number | null;
  negotiation_stage: NegotiationStage | null;
  operator_override: boolean;
}

export interface NegotiationMessage {
  id: string;
  bid_id: string;
  sender: "ai" | "client" | "operator";
  content: string;
  type: "message" | "proposal" | "counter_offer" | "system";
  timestamp: string;
}

export interface NegotiationDetail {
  bid_id: string;
  stage: NegotiationStage;
  operator_override: boolean;
  messages_count: number;
  last_activity: string | null;
}

// --- Project Detail ---

export interface ProjectTask {
  id: string;
  title: string;
  agent: string | null;
  status: "pending" | "in_progress" | "completed" | "failed";
  estimated_hours: number | null;
  actual_hours: number | null;
}

export interface ProjectArtifact {
  id: string;
  filename: string;
  type: string;
  size_bytes: number | null;
  url: string | null;
  created_at: string;
}

export interface ProjectRevision {
  id: string;
  revision_number: number;
  description: string;
  agent: string | null;
  created_at: string;
}

export interface ProjectDetail extends Project {
  tasks: ProjectTask[];
  artifacts_list: ProjectArtifact[];
  revisions: ProjectRevision[];
  agreed_amount: number | null;
  revision_count: number;
  type: string | null;
}
