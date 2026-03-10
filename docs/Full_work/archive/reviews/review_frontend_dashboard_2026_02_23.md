# Frontend Dashboard Code Review

**Date:** 2026-02-23
**Scope:** `dashboard/app/` -- API client, types, WebSocket hooks, Zustand stores, auth guard, route quality, build config
**Reviewer:** Research Worker (Claude Opus 4.6)

---

## Summary

The MAS Remix dashboard is a well-structured SPA with solid fundamentals: consistent error boundaries across all routes, proper loading/empty/error states, and a cohesive component library. However, there are several production-impacting issues, primarily around token refresh race conditions, WebSocket event listener leaks, and missing frontend test infrastructure.

**Findings by severity:**
- CRITICAL: 3
- HIGH: 6
- MEDIUM: 7
- LOW: 4

---

## CRITICAL Findings

### C1. Token Refresh Race Condition -- No Request Queuing

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/lib/api.ts`, lines 95-172
**Severity:** CRITICAL

When multiple concurrent requests receive 401 responses, each independently calls `refreshAccessToken()`. This creates a race condition:

1. Request A gets 401, starts refreshing token
2. Request B gets 401, starts refreshing token with the SAME refresh token
3. If the backend invalidates refresh tokens on use (standard practice), Request B's refresh will fail
4. `clearAuth()` fires, user is logged out unexpectedly

The `refreshAccessToken()` function at line 95 has no mutex, no promise deduplication, no request queuing.

**Impact:** Users get randomly logged out during periods of high API activity (e.g., dashboard page loading 7 queries simultaneously).

**Recommended fix:**
```typescript
let refreshPromise: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    try {
      const refreshToken = getRefreshToken();
      if (!refreshToken) return null;
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
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}
```

---

### C2. WebSocket Event Listener Leak in `useWsLogStream`

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/hooks/use-ws-log-stream.ts`, lines 121-137, 145-150
**Severity:** CRITICAL

The `useWsLogStream` hook adds `message` and `close` event listeners to the shared global WebSocket at line 121/137, but the cleanup function (lines 145-150) only clears the reconnect timeout -- it never removes the event listeners:

```typescript
// Cleanup at lines 145-150:
return () => {
  if (reconnectTimeoutRef.current) {
    clearTimeout(reconnectTimeoutRef.current);
  }
  setIsConnected(false);
};
```

The `handleMessage` listener references `agentName` via closure, but this listener persists after unmount. The `mountedRef` check at line 79 prevents state updates, but:
1. JSON parsing and object allocation still happen for every WS message
2. Event listeners accumulate on each mount/unmount cycle (e.g., navigating to different agent pages)
3. After many navigations, hundreds of dead listeners process every WebSocket message

**Impact:** Memory leak and CPU waste that degrades over time. In a long-running session, noticeable UI jank.

**Recommended fix:** Store a reference to the `handleMessage` and `handleClose` listeners and remove them in the cleanup:

```typescript
const listenersRef = useRef<{ message?: (e: MessageEvent) => void; close?: () => void }>({});

// In connect():
listenersRef.current.message = handleMessage;
listenersRef.current.close = handleClose;
ws.addEventListener("message", handleMessage);
ws.addEventListener("close", handleClose);

// In cleanup:
return () => {
  const ws = wsRef.current;
  if (ws) {
    if (listenersRef.current.message) ws.removeEventListener("message", listenersRef.current.message);
    if (listenersRef.current.close) ws.removeEventListener("close", listenersRef.current.close);
  }
  // ... existing cleanup
};
```

---

### C3. No Frontend Test Infrastructure

**File:** `/Users/awon/programming/projects/MAS/dashboard/package.json`
**Severity:** CRITICAL

The `package.json` has zero test-related dependencies. No vitest, jest, testing-library, playwright, or cypress. There is no `test` script in the `scripts` section. The `devDependencies` contain only build tooling.

For a dashboard that handles:
- Authentication flows (token storage, refresh, logout)
- Financial actions (bid approvals via HITL)
- Credential management (API keys, platform passwords)
- Bulk operations (bulk resolve HITL)

The absence of any testing means regressions in these areas would ship silently.

**Impact:** Any code change to auth, HITL resolution, or settings can break production with zero automated protection.

**Recommended fix (minimum viable):**
1. Add `vitest` + `@testing-library/react` + `jsdom`
2. Write tests for: `api.ts` (token refresh dedup), `auth-store.ts` (login/logout), `useWsLogStream` (cleanup)
3. Add `"test": "vitest run"` to scripts

---

## HIGH Findings

### H1. `useWsSubscription` Never Sends Unsubscribe on Unmount

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/hooks/use-ws-subscription.ts`, lines 18-44
**Severity:** HIGH

The hook sends `subscribe:agent` or `subscribe:project` messages when mounting, but never sends a corresponding unsubscribe when the component unmounts. If the backend tracks subscriptions per-connection, the server accumulates subscriptions that nobody consumes.

Additionally, the `onMessage` callback parameter is accepted but never used -- there is no `addEventListener("message", ...)` call in the hook body. It is dead code.

**Impact:** Server-side resource waste; the `onMessage` parameter misleads developers into thinking per-component message handling works.

**Recommended fix:** Send an unsubscribe message in the cleanup function. Either implement the `onMessage` handler or remove the dead parameter.

---

### H2. `_app.tsx` Layout Missing ErrorBoundary Export

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/routes/_app.tsx`
**Severity:** HIGH

The `_app.tsx` layout route does not export an `ErrorBoundary`. While all child routes have one, if the layout itself throws (e.g., the WebSocket `onopen` throws, or `useAuthStore` throws during hydration), the error bubbles up to Remix's root error handler, which may show a blank page.

All 15 child routes export `RouteErrorBoundary`, but the parent layout at `_app.tsx` does not.

**Impact:** Unhandled errors in the layout (WebSocket, auth store, query client) cause full-page white screen.

**Recommended fix:** Add `export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";` to `_app.tsx`.

---

### H3. `window.__masWs` Global Pattern is Fragile

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/routes/_app.tsx`, line 128
**File:** `/Users/awon/programming/projects/MAS/dashboard/app/hooks/use-ws-log-stream.ts`, lines 52-53
**File:** `/Users/awon/programming/projects/MAS/dashboard/app/hooks/use-ws-subscription.ts`, line 22
**Severity:** HIGH

The WebSocket instance is shared via `(window as unknown as Record<string, unknown>).__masWs`, a type-unsafe global property. This pattern:

1. Requires repeated `as unknown as Record<string, unknown>` casts (3 files)
2. Has no TypeScript compile-time safety
3. Is invisible to React's rendering lifecycle -- components cannot react to WS connection changes
4. Creates a timing dependency: hooks poll with `setInterval` or `setTimeout` hoping the global is set

**Impact:** Brittle hook initialization; intermittent "not connected" states on slow connections; maintenance burden from type casts.

**Recommended fix:** Use React Context to provide the WebSocket instance:
```typescript
const WsContext = createContext<WebSocket | null>(null);
// In _app.tsx: <WsContext.Provider value={wsRef.current}>
// In hooks: const ws = useContext(WsContext);
```

---

### H4. Auth Guard Has Flash-of-Content Before Redirect

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/routes/_app.tsx`, lines 102-106, 322-324
**Severity:** HIGH

The auth guard is implemented as a `useEffect`:
```typescript
useEffect(() => {
  if (!isAuthenticated) {
    navigate("/login", { replace: true });
  }
}, [isAuthenticated, navigate]);
```

And at line 322: `if (!isAuthenticated) return null;`

Problem: On first render, Zustand hydrates from localStorage asynchronously via the `persist` middleware. During hydration, `isAuthenticated` is `false` (the default). This means:

1. First render: `isAuthenticated = false` -> returns `null` (blank flash)
2. `useEffect` fires -> navigates to `/login`
3. Zustand hydrates -> sets `isAuthenticated = true`
4. But user is already on `/login`

For returning users with valid tokens, this causes an unnecessary redirect to `/login` followed by a redirect back to `/dashboard`.

**Impact:** Brief white flash on page load; double navigation for authenticated users on hard refresh.

**Recommended fix:** Add a `hasHydrated` state to the auth store and show a loading spinner until hydration completes:
```typescript
// In auth-store.ts:
interface AuthState {
  // ...existing fields
  _hasHydrated: boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      // ...existing state
      _hasHydrated: false,
    }),
    {
      name: "auth-storage",
      onRehydrateStorage: () => () => {
        useAuthStore.setState({ _hasHydrated: true });
      },
    }
  )
);
```

---

### H5. Settings Page Bypasses `apiFetch` for Telegram Linking

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/routes/_app.settings.tsx`, lines 152-166
**Severity:** HIGH

The `handleTelegramLink` function manually constructs the API URL and Authorization header instead of using `apiFetch`:

```typescript
const token = useAuthStore.getState().accessToken;
const res = await fetch(`${apiBase}/auth/telegram/link`, { ... });
```

This means:
1. No 401 auto-refresh -- if the token expired, the request silently fails
2. No timeout protection (the 15-second abort controller from `apiFetch` is bypassed)
3. No consistent error handling format

**Impact:** Telegram linking fails silently if the token expired; no timeout protection.

**Recommended fix:** Use `apiFetch` instead of raw `fetch`:
```typescript
const result = await apiFetch("/auth/telegram/link", {
  method: "POST",
  body: JSON.stringify({ code: telegramCode }),
});
```

---

### H6. Settings Page Uses Manual Data Fetching Instead of React Query

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/routes/_app.settings.tsx`, lines 97-137
**Severity:** HIGH

The settings page manages its own loading/error state with `useState` + `useEffect` + manual `fetchCredentials()` calls, while every other page in the app uses React Query. This creates:

1. No automatic cache invalidation when credentials change from another tab
2. No stale-while-revalidate behavior
3. No automatic retry on failure
4. Inconsistent patterns (Zustand + manual fetch vs React Query everywhere else)

Lines 97-99:
```typescript
const [credentials, setCredentials] = useState<CredentialsSummary | null>(null);
const [loadingCredentials, setLoadingCredentials] = useState(true);
```

**Impact:** Stale credentials data; no automatic refresh; inconsistent developer experience.

**Recommended fix:** Replace with `useQuery`:
```typescript
const { data: credentials, isLoading: loadingCredentials } = useQuery({
  queryKey: ["credentials"],
  queryFn: fetchCredentials,
  staleTime: 60_000,
});
```

---

## MEDIUM Findings

### M1. `any` Types in Frontend Type Definitions

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/lib/types.ts`, lines 41, 104, 130, 177, 293
**File:** `/Users/awon/programming/projects/MAS/dashboard/app/lib/api.ts`, lines 547, 561-562
**Severity:** MEDIUM

Five interface fields use `Record<string, any>`:
- `HITLItem.payload` (line 41)
- `AgentLog.details` (line 104)
- `Job.client_info` (line 130)
- `LeadDetail.enrichment_data` (line 177)
- `PlatformAccount.stats` (line 293)

And `api.ts` has two `Record<string, any>` in function signatures (lines 547, 561-562).

Additionally, the settings page uses `(window as any)` at lines 762 and 768.

**Impact:** Loss of type safety; runtime errors from unexpected data shapes; TypeScript's value is diminished.

**Recommended fix:** Define specific interfaces for known payload shapes (at minimum for `HITLItem.payload` which has known action types). Use `Record<string, unknown>` instead of `Record<string, any>` for truly dynamic data.

---

### M2. No OpenAPI Auto-Generation for Types

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/lib/types.ts` (all 378 lines)
**Severity:** MEDIUM

All 30+ TypeScript interfaces are manually written and maintained separately from the backend Pydantic models. There is no `openapi-typescript`, `orval`, or similar tool to auto-generate types from the backend's OpenAPI schema.

**Impact:** Type drift between frontend and backend is inevitable. When a backend field is added/renamed/removed, the frontend will silently use stale types. This has likely already happened (no audit trail).

**Recommended fix:**
1. Export OpenAPI JSON from the Litestar backend (`/schema` endpoint)
2. Add `openapi-typescript` to devDependencies
3. Add script: `"generate-types": "openapi-typescript http://localhost:8000/schema -o app/lib/api-types.ts"`
4. Run before builds in CI

---

### M3. Retry Button on Dashboard Invalidates ALL Queries

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/routes/_app.dashboard.tsx`, line 254
**Severity:** MEDIUM

```typescript
<Button onClick={() => queryClient.invalidateQueries()}>Retry</Button>
```

`queryClient.invalidateQueries()` with no arguments invalidates every cached query in the entire application -- not just the dashboard queries. This causes a thundering herd of API requests across all mounted components.

**Impact:** Unnecessary API load; potential rate limiting on the backend; UI flicker across all mounted components.

**Recommended fix:** Invalidate only dashboard-related query keys:
```typescript
onClick={() => {
  queryClient.invalidateQueries({ queryKey: ["agent-status"] });
  queryClient.invalidateQueries({ queryKey: ["hitl-stats"] });
  queryClient.invalidateQueries({ queryKey: ["job-stats"] });
  queryClient.invalidateQueries({ queryKey: ["jobs"] });
  queryClient.invalidateQueries({ queryKey: ["hitl-trends"] });
}}
```

---

### M4. Dashboard Page Fires 7 Parallel API Requests on Every Mount

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/routes/_app.dashboard.tsx`, lines 21-67
**Severity:** MEDIUM

The dashboard page creates 7 independent `useQuery` hooks that all fire on mount:
1. `agent-status`
2. `hitl-stats`
3. `jobs` (active, limit 50)
4. `job-stats`
5. `hitl-trends`
6. `pipeline-b-stats`
7. `credentials-summary`

All have `staleTime` of 10-60 seconds, so returning to the dashboard after a minute triggers all 7 again.

**Impact:** High API load on every dashboard visit; potential for HTTP/2 connection limit issues; slow initial paint waiting for all queries.

**Recommended fix:** Consider a combined dashboard endpoint on the backend (`/api/v1/dashboard/summary`) that returns aggregated data in a single request. Alternatively, increase `staleTime` for less-critical queries (credentials, pipeline-b-stats) to 5+ minutes.

---

### M5. `Content-Type: application/json` Set Even for GET Requests

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/lib/api.ts`, lines 124-127
**Severity:** MEDIUM

The `apiFetch` function always sets `Content-Type: application/json` in headers, including for GET requests that have no body:

```typescript
const headers: Record<string, string> = {
  "Content-Type": "application/json",
  ...(options.headers as Record<string, string>),
};
```

While most servers ignore this, some proxies and CDNs may behave unexpectedly. The `Content-Type` header on a GET request is technically meaningless per HTTP spec and some strict WAFs may reject it.

**Impact:** Minor; potential issues with strict proxies/WAFs.

**Recommended fix:** Only set `Content-Type` when there is a request body:
```typescript
const headers: Record<string, string> = {
  ...(options.headers as Record<string, string>),
};
if (options.body) {
  headers["Content-Type"] = "application/json";
}
```

---

### M6. WebSocket Does Not Re-Authenticate After Reconnect with New Token

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/routes/_app.tsx`, lines 111-285
**Severity:** MEDIUM

The `connectWs` function is `useCallback` with `accessToken` as a dependency (line 286). When the access token is refreshed via `refreshAccessToken()` in `api.ts`, the token stored in Zustand changes, but this does NOT trigger a WebSocket reconnection.

The WebSocket uses the token that was current at `connectWs` invocation time. If the token was refreshed (e.g., after a 401), the WebSocket continues using the old auth context until it disconnects and reconnects.

**Impact:** After token refresh, WebSocket events may be received under the old auth context. If the backend validates WS auth per-message, this could cause dropped events.

**Recommended fix:** Add `accessToken` to the dependency array of the connection `useEffect`, or send a re-auth message when the token changes.

---

### M7. AbortController Reuse After Timeout on Retry

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/lib/api.ts`, lines 121-149
**Severity:** MEDIUM

When a 401 triggers a retry (lines 141-153), the retry `fetch` reuses the same `AbortController`'s signal:

```typescript
const controller = new AbortController();
const timeoutId = setTimeout(() => controller.abort(), 15_000);
// ...first fetch uses controller.signal
// ...on 401, second fetch ALSO uses the same controller.signal
```

If the first request took 14 seconds before returning 401, the retry only has 1 second before the shared `AbortController` fires. The timeout is from the creation of the controller, not from the start of the retry.

**Impact:** Retry requests may be immediately aborted if the original request was slow.

**Recommended fix:** Create a new `AbortController` for the retry request.

---

## LOW Findings

### L1. Notification Store Not Persisted

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/stores/notification-store.ts`
**Severity:** LOW

Unlike `auth-store` and `theme-store`, the notification store does not use Zustand's `persist` middleware. Notifications are lost on page refresh.

**Impact:** Minor UX issue; users lose notification history on refresh. The 50-item cap and ephemeral nature of notifications make this acceptable, but worth noting.

---

### L2. Hardcoded Version Fallback in Settings

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/routes/_app.settings.tsx`, line 762
**Severity:** LOW

```typescript
{(typeof window !== "undefined" && (window as any).ENV?.APP_VERSION) || "4.2.0"}
```

The version "4.2.0" is hardcoded as a fallback. This will become stale as the project evolves.

**Impact:** Misleading version display if `APP_VERSION` env var is not set.

**Recommended fix:** Source the version from `package.json` at build time via Vite's `define` config.

---

### L3. `useCountUp` and `useKeyboardShortcuts` Hooks Not Reviewed

**Files:**
- `/Users/awon/programming/projects/MAS/dashboard/app/hooks/use-count-up.ts`
- `/Users/awon/programming/projects/MAS/dashboard/app/hooks/use-keyboard-shortcuts.ts`

**Severity:** LOW

These hooks were present but not part of the explicit review scope. `useKeyboardShortcuts` is used in the layout but its implementation was not audited for potential issues (e.g., global event listeners not cleaned up).

---

### L4. `login()` and `register()` Functions Don't Use `apiFetch`

**File:** `/Users/awon/programming/projects/MAS/dashboard/app/lib/api.ts`, lines 176-215
**Severity:** LOW

The `login()` and `register()` functions use raw `fetch` instead of `apiFetch`. This is intentional (no auth token exists yet), but they miss the timeout protection (15-second abort) that `apiFetch` provides.

**Impact:** Login/register requests could hang indefinitely on a slow network.

**Recommended fix:** Add a timeout `AbortController` to these functions as well.

---

## Positive Observations

The following patterns are well-implemented and worth preserving:

1. **Consistent ErrorBoundary exports** -- All 15 route files export `RouteErrorBoundary`. This is excellent defensive programming.

2. **Loading skeletons everywhere** -- Every data-fetching page shows proper skeleton loading states with `isLoading && !data` guards to avoid flickering on refetch.

3. **WebSocket exponential backoff** -- The reconnection logic in `_app.tsx` uses proper exponential backoff capped at 30 seconds with reset on successful connection.

4. **Desktop notifications for HITL** -- Browser Notification API integration for critical HITL events is production-quality with deduplication (`tag: "mas-hitl"`), auto-close, and focus-based gating.

5. **Debounced search** -- HITL page properly debounces search input (300ms) and resets pagination on search change.

6. **Bulk action UX** -- The HITL bulk select/action flow with sticky bottom bar, loading states, and clear selection is well-designed.

7. **Zustand stores are lean** -- The three stores (auth, theme, notification) are minimal and focused. No state duplication between stores and React Query.

8. **Vite proxy configuration** -- Clean dev proxy setup for API and WebSocket passthrough avoids CORS issues locally.

---

## Architecture Recommendations

### Short-term (next sprint)
1. Fix C1 (token refresh race condition) -- highest impact, affects all authenticated users
2. Fix C2 (event listener leak) -- affects long-running sessions
3. Fix H2 (layout ErrorBoundary) -- 1-line addition, high safety value
4. Fix H5 (Telegram bypass) -- 5-minute fix

### Medium-term (next month)
1. Add vitest + testing-library (C3)
2. Replace `window.__masWs` with React Context (H3)
3. Migrate settings page to React Query (H6)
4. Add OpenAPI type generation (M2)

### Long-term (next quarter)
1. Add E2E tests with Playwright for auth flows and HITL resolution
2. Consider server-side rendering for initial auth check (eliminate hydration flash)
3. Implement combined dashboard API endpoint (M4)
