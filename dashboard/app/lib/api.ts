import type {
  LoginResponse,
  HITLPendingResponse,
  HITLResolveResponse,
  HITLStats,
  AgentStatusList,
} from "./types";

const API_BASE = "/api/v1";

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
    const res = await fetch(`${API_BASE}/auth/refresh`, {
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

  let res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  // On 401, try to refresh and retry once
  if (res.status === 401 && token) {
    const newToken = await refreshAccessToken();
    if (newToken) {
      headers["Authorization"] = `Bearer ${newToken}`;
      res = await fetch(`${API_BASE}${path}`, {
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
  const res = await fetch(`${API_BASE}/auth/login`, {
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
  notes?: string
): Promise<HITLResolveResponse> {
  return apiFetch<HITLResolveResponse>(`/hitl/${id}/resolve`, {
    method: "POST",
    body: JSON.stringify({ action, notes }),
  });
}

export async function fetchHITLStats(): Promise<HITLStats> {
  return apiFetch<HITLStats>("/hitl/stats");
}

// --- Agents ---

export async function fetchAgentStatus(): Promise<AgentStatusList> {
  return apiFetch<AgentStatusList>("/agents/status");
}
