import type {
  LoginResponse,
  RegisterPendingResponse,
  HITLPendingResponse,
  HITLResolveResponse,
  HITLStats,
  AgentStatusList,
  AgentLogList,
  Job,
  JobListResponse,
  JobStats,
  UserListResponse,
  User,
  LeadListResponse,
  PipelineBStats,
  ScanResponse,
  OrchestratorStatus,
  GoalListResponse,
  HealthReport,
  Phase,
  LogResponse,
  CredentialsSummary,
  PlatformAccount,
} from "./types";

declare global {
  interface Window {
    ENV?: { API_URL?: string };
  }
}

function getApiBase(): string {
  if (typeof window !== "undefined" && window.ENV?.API_URL) {
    return `${window.ENV.API_URL}/api/v1`;
  }
  return "/api/v1";
}

function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem("auth-storage");
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed?.state?.accessToken ?? null;
  } catch {
    return null;
  }
}

function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem("auth-storage");
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed?.state?.refreshToken ?? null;
  } catch {
    return null;
  }
}

function updateTokens(accessToken: string, refreshToken: string) {
  if (typeof window === "undefined") return;
  try {
    const raw = localStorage.getItem("auth-storage");
    if (!raw) return;
    const parsed = JSON.parse(raw);
    parsed.state.accessToken = accessToken;
    parsed.state.refreshToken = refreshToken;
    localStorage.setItem("auth-storage", JSON.stringify(parsed));
  } catch {
    // noop
  }
}

function clearAuth() {
  if (typeof window === "undefined") return;
  localStorage.removeItem("auth-storage");
  window.location.href = "/login";
}

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;

  try {
    const res = await fetch(`${getApiBase()}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    if (!res.ok) return null;

    const data = await res.json();
    updateTokens(data.access_token, data.refresh_token);
    return data.access_token;
  } catch {
    return null;
  }
}

async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getAccessToken();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  let res = await fetch(`${getApiBase()}${path}`, {
    ...options,
    headers,
  });

  // On 401, try to refresh and retry once
  if (res.status === 401 && token) {
    const newToken = await refreshAccessToken();
    if (newToken) {
      headers["Authorization"] = `Bearer ${newToken}`;
      res = await fetch(`${getApiBase()}${path}`, {
        ...options,
        headers,
      });
    } else {
      clearAuth();
      throw new Error("Session expired");
    }
  }

  if (!res.ok) {
    const errorBody = await res.text();
    throw new Error(
      `API Error ${res.status}: ${errorBody || res.statusText}`
    );
  }

  return res.json();
}

// --- Auth ---

export async function login(
  email: string,
  password: string
): Promise<LoginResponse> {
  const res = await fetch(`${getApiBase()}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  if (!res.ok) {
    const errorBody = await res.text();
    throw new Error(
      `Login failed: ${errorBody || res.statusText}`
    );
  }

  return res.json();
}

export async function register(
  email: string,
  password: string,
  name?: string
): Promise<LoginResponse | RegisterPendingResponse> {
  const res = await fetch(`${getApiBase()}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, name: name || undefined }),
  });

  if (!res.ok && res.status !== 202) {
    const errorBody = await res.text();
    throw new Error(
      `Registration failed: ${errorBody || res.statusText}`
    );
  }

  return res.json();
}

// --- HITL ---

export async function fetchHITLPending(params?: {
  type?: string;
  limit?: number;
  offset?: number;
}): Promise<HITLPendingResponse> {
  const searchParams = new URLSearchParams();
  if (params?.type) searchParams.set("type", params.type);
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.offset) searchParams.set("offset", String(params.offset));

  const query = searchParams.toString();
  return apiFetch<HITLPendingResponse>(
    `/hitl/pending${query ? `?${query}` : ""}`
  );
}

export async function resolveHITL(
  id: string,
  action: string,
  note?: string
): Promise<HITLResolveResponse> {
  return apiFetch<HITLResolveResponse>(`/hitl/${id}/resolve`, {
    method: "POST",
    body: JSON.stringify({ action, note }),
  });
}

export async function fetchHITLStats(): Promise<HITLStats> {
  return apiFetch<HITLStats>("/hitl/stats");
}

// --- Agents ---

export async function fetchAgentStatus(): Promise<AgentStatusList> {
  return apiFetch<AgentStatusList>("/agents/status");
}

export async function fetchAgentLogs(
  name: string,
  params?: { level?: string; limit?: number; offset?: number }
): Promise<AgentLogList> {
  const searchParams = new URLSearchParams();
  if (params?.level) searchParams.set("level", params.level);
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.offset) searchParams.set("offset", String(params.offset));

  const query = searchParams.toString();
  return apiFetch<AgentLogList>(
    `/agents/${name}/logs${query ? `?${query}` : ""}`
  );
}

export async function restartAgent(name: string) {
  return apiFetch<{ agent: string; action: string; status: string; message: string }>(
    `/agents/${name}/restart`,
    { method: "POST" }
  );
}

export async function pauseAgent(name: string) {
  return apiFetch<{ agent: string; action: string; status: string; message: string }>(
    `/agents/${name}/pause`,
    { method: "POST" }
  );
}

export async function resumeAgent(name: string) {
  return apiFetch<{ agent: string; action: string; status: string; message: string }>(
    `/agents/${name}/resume`,
    { method: "POST" }
  );
}

// --- Jobs ---

export async function fetchJobs(params?: {
  status?: string;
  platform?: string;
  min_score?: number;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<JobListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  if (params?.platform) searchParams.set("platform", params.platform);
  if (params?.min_score != null) searchParams.set("min_score", String(params.min_score));
  if (params?.search) searchParams.set("search", params.search);
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.offset) searchParams.set("offset", String(params.offset));

  const query = searchParams.toString();
  return apiFetch<JobListResponse>(
    `/jobs${query ? `?${query}` : ""}`
  );
}

export async function fetchJob(id: string): Promise<Job> {
  return apiFetch<Job>(`/jobs/${id}`);
}

export async function fetchJobStats(): Promise<JobStats> {
  return apiFetch<JobStats>("/jobs/stats");
}

export async function disqualifyJob(id: string, reason: string) {
  return apiFetch<{ id: string; status: string; disqualify_reason: string }>(
    `/jobs/${id}/disqualify`,
    {
      method: "POST",
      body: JSON.stringify({ reason }),
    }
  );
}

// --- Users (owner-only) ---

export async function fetchUsers(params?: {
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<UserListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.offset) searchParams.set("offset", String(params.offset));

  const query = searchParams.toString();
  return apiFetch<UserListResponse>(
    `/users${query ? `?${query}` : ""}`
  );
}

export async function approveUser(id: string, role: string): Promise<User> {
  return apiFetch<User>(`/users/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ role }),
  });
}

export async function rejectUser(id: string): Promise<User> {
  return apiFetch<User>(`/users/${id}/reject`, {
    method: "POST",
  });
}

export async function updateUserRole(id: string, role: string): Promise<User> {
  return apiFetch<User>(`/users/${id}/role`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

export async function updateUserStatus(id: string, status: string): Promise<User> {
  return apiFetch<User>(`/users/${id}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export async function deleteUser(id: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/users/${id}`, {
    method: "DELETE",
  });
}

export async function transferOwnership(userId: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/users/${userId}/transfer-ownership`, {
    method: "POST",
  });
}

// --- Pipeline B ---

export async function fetchLeads(params?: {
  city?: string;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<LeadListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.city) searchParams.set("city", params.city);
  if (params?.status) searchParams.set("status", params.status);
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.offset) searchParams.set("offset", String(params.offset));

  const query = searchParams.toString();
  return apiFetch<LeadListResponse>(
    `/pipeline-b/leads${query ? `?${query}` : ""}`
  );
}

export async function fetchPipelineBStats(): Promise<PipelineBStats> {
  return apiFetch<PipelineBStats>("/pipeline-b/stats");
}

export async function startScan(city: string): Promise<ScanResponse> {
  return apiFetch<ScanResponse>("/pipeline-b/scan", {
    method: "POST",
    body: JSON.stringify({ city }),
  });
}

// --- Orchestrator ---

export async function fetchOrchestratorStatus(): Promise<OrchestratorStatus> {
  return apiFetch<OrchestratorStatus>("/orchestrator/status");
}

export async function startOrchestrator(): Promise<{ status: string; pid: number; message: string }> {
  return apiFetch("/orchestrator/start", {
    method: "POST",
  });
}

export async function stopOrchestrator(): Promise<{ status: string; message: string }> {
  return apiFetch("/orchestrator/stop", { method: "POST" });
}

export async function fetchGoals(params?: {
  status?: string;
}): Promise<GoalListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  const query = searchParams.toString();
  return apiFetch<GoalListResponse>(`/orchestrator/goals${query ? `?${query}` : ""}`);
}

export async function addGoal(
  title: string,
  priority?: string,
  category?: string
): Promise<{ id: string; title: string; message: string }> {
  return apiFetch("/orchestrator/goals", {
    method: "POST",
    body: JSON.stringify({ title, priority: priority ?? "medium", category: category ?? "feature" }),
  });
}

export async function deleteGoal(
  goalId: string
): Promise<{ goal_id: string; message: string }> {
  return apiFetch(`/orchestrator/goals/${goalId}`, {
    method: "DELETE",
  });
}

export async function fetchHealth(): Promise<HealthReport> {
  return apiFetch<HealthReport>("/orchestrator/health");
}

export async function fetchMilestones(): Promise<Phase[]> {
  return apiFetch<Phase[]>("/orchestrator/milestones");
}

export async function fetchOrchestratorLogs(params?: { n?: number }): Promise<LogResponse> {
  const searchParams = new URLSearchParams();
  if (params?.n) searchParams.set("n", String(params.n));
  const query = searchParams.toString();
  return apiFetch<LogResponse>(`/orchestrator/logs${query ? `?${query}` : ""}`);
}

// --- Settings ---

export async function fetchCredentials(): Promise<CredentialsSummary> {
  return apiFetch<CredentialsSummary>("/settings/credentials");
}

export async function fetchPlatformAccounts(): Promise<{ accounts: PlatformAccount[]; total: number }> {
  return apiFetch<{ accounts: PlatformAccount[]; total: number }>("/settings/platform-accounts");
}

export async function createPlatformAccount(data: {
  platform: string;
  username?: string;
  credentials: Record<string, string>;
  profile_url?: string;
}): Promise<PlatformAccount> {
  return apiFetch<PlatformAccount>("/settings/platform-accounts", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updatePlatformAccount(
  id: string,
  data: Record<string, any>
): Promise<PlatformAccount> {
  return apiFetch<PlatformAccount>(`/settings/platform-accounts/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deletePlatformAccount(id: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/settings/platform-accounts/${id}`, {
    method: "DELETE",
  });
}

export async function saveAPIKeys(keys: Record<string, string>): Promise<{ api_keys: Record<string, any>; message: string }> {
  return apiFetch<{ api_keys: Record<string, any>; message: string }>("/settings/api-keys", {
    method: "PUT",
    body: JSON.stringify(keys),
  });
}
