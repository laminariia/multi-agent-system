import { useEffect, useRef, useCallback } from "react";
import { Outlet, NavLink, useNavigate, useLocation } from "@remix-run/react";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { useAuthStore } from "~/stores/auth-store";
import { fetchHITLPending } from "~/lib/api";
import { useQuery } from "@tanstack/react-query";

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout>>();

  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const user = useAuthStore((s) => s.user);
  const accessToken = useAuthStore((s) => s.accessToken);
  const logout = useAuthStore((s) => s.logout);

  // Fetch pending count for sidebar badge
  const { data: hitlData } = useQuery({
    queryKey: ["hitl-pending-count"],
    queryFn: () => fetchHITLPending({ limit: 1 }),
    refetchInterval: 30_000,
    enabled: isAuthenticated,
  });

  // Auth guard
  useEffect(() => {
    if (!isAuthenticated) {
      navigate("/login", { replace: true });
    }
  }, [isAuthenticated, navigate]);

  // WebSocket connection
  const connectWs = useCallback(() => {
    if (!accessToken) return;

    // Use relative ws URL to go through Vite proxy
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/events`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        // Send auth message
        ws.send(
          JSON.stringify({
            type: "auth",
            token: `Bearer ${accessToken}`,
          })
        );
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);

          if (
            msg.type === "hitl:new" ||
            msg.type === "hitl:resolved"
          ) {
            queryClient.invalidateQueries({ queryKey: ["hitl-pending"] });
            queryClient.invalidateQueries({
              queryKey: ["hitl-pending-count"],
            });
            queryClient.invalidateQueries({ queryKey: ["hitl-stats"] });
          }

          if (msg.type === "agent:heartbeat") {
            queryClient.invalidateQueries({
              queryKey: ["agent-status"],
            });
          }
        } catch {
          // ignore non-JSON messages
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        // Reconnect after 3 seconds
        reconnectTimeoutRef.current = setTimeout(connectWs, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      // Retry on connection failure
      reconnectTimeoutRef.current = setTimeout(connectWs, 5000);
    }
  }, [accessToken, queryClient]);

  useEffect(() => {
    connectWs();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
    };
  }, [connectWs]);

  const handleLogout = () => {
    if (wsRef.current) {
      wsRef.current.close();
    }
    logout();
    navigate("/login", { replace: true });
  };

  if (!isAuthenticated) {
    return null;
  }

  // Breadcrumb from location
  const pathSegments = location.pathname
    .split("/")
    .filter(Boolean)
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1));

  const pendingCount = hitlData?.total ?? 0;
  const urgentCount = hitlData?.pending_urgent ?? 0;

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="flex w-64 flex-col border-r border-slate-700/50 bg-slate-900/50">
        {/* Logo */}
        <div className="flex h-14 items-center border-b border-slate-700/50 px-5">
          <span className="text-xl font-bold tracking-tight text-primary">
            MAS
          </span>
          <span className="text-xs font-medium text-muted-foreground ml-2 mt-0.5">
            Multi-Agent Service
          </span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-4 space-y-1">
          <NavLink
            to="/hitl"
            className={({ isActive }) =>
              `flex items-center justify-between rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                isActive
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground"
              }`
            }
          >
            <span className="flex items-center gap-2.5">
              <svg
                className="h-4 w-4"
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
                <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
                <path d="M16 3.13a4 4 0 0 1 0 7.75" />
              </svg>
              HITL Queue
            </span>
            {pendingCount > 0 && (
              <Badge
                variant={urgentCount > 0 ? "destructive" : "default"}
                className="h-5 min-w-[20px] justify-center text-[10px] px-1.5"
              >
                {pendingCount}
              </Badge>
            )}
          </NavLink>

          <NavLink
            to="/agents"
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                isActive
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground"
              }`
            }
          >
            <svg
              className="h-4 w-4"
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect width="18" height="18" x="3" y="3" rx="2" />
              <path d="M3 9h18" />
              <path d="M9 21V9" />
            </svg>
            Agents
          </NavLink>
        </nav>

        {/* User section at bottom */}
        <div className="border-t border-slate-700/50 p-3">
          <div className="flex items-center justify-between rounded-md px-2 py-1.5">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-foreground">
                {user?.name || user?.email || "User"}
              </p>
              {user?.name && (
                <p className="truncate text-xs text-muted-foreground">
                  {user.email}
                </p>
              )}
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleLogout}
              className="flex-shrink-0 text-muted-foreground hover:text-foreground"
            >
              <svg
                className="h-4 w-4"
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
            </Button>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex h-14 items-center border-b border-slate-700/50 px-6">
          <nav className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <span className="text-foreground">Dashboard</span>
            {pathSegments.map((segment, i) => (
              <span key={i} className="flex items-center gap-1.5">
                <svg
                  className="h-3 w-3"
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="m9 18 6-6-6-6" />
                </svg>
                <span
                  className={
                    i === pathSegments.length - 1
                      ? "text-foreground font-medium"
                      : ""
                  }
                >
                  {segment}
                </span>
              </span>
            ))}
          </nav>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
