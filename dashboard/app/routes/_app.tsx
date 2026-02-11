import { useEffect, useRef, useCallback, useState } from "react";
import { Outlet, useNavigate, useLocation } from "@remix-run/react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "~/components/ui/button";
import { Avatar, AvatarFallback } from "~/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import { SidebarNav } from "~/components/sidebar-nav";
import { useAuthStore } from "~/stores/auth-store";
import { useThemeStore } from "~/stores/theme-store";
import { fetchHITLPending, fetchUsers } from "~/lib/api";
import { useQuery } from "@tanstack/react-query";
import { toast } from "~/hooks/use-toast";

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout>>();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const user = useAuthStore((s) => s.user);
  const accessToken = useAuthStore((s) => s.accessToken);
  const logout = useAuthStore((s) => s.logout);
  const theme = useThemeStore((s) => s.theme);
  const toggleTheme = useThemeStore((s) => s.toggleTheme);

  // Fetch pending count for sidebar badge
  const { data: hitlData } = useQuery({
    queryKey: ["hitl-pending-count"],
    queryFn: () => fetchHITLPending({ limit: 1 }),
    refetchInterval: 30_000,
    enabled: isAuthenticated,
  });

  // Fetch pending users count for sidebar badge (owner only)
  const { data: pendingUsersData } = useQuery({
    queryKey: ["pending-users-count"],
    queryFn: () => fetchUsers({ status: "pending_approval", limit: 1 }),
    refetchInterval: 60_000,
    enabled: isAuthenticated && (user?.role === "owner" || user?.role === "co_owner"),
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

    const apiUrl = window.ENV?.API_URL;
    const wsHost = apiUrl
      ? new URL(apiUrl).host
      : window.location.host;
    const protocol = (apiUrl?.startsWith("https") || window.location.protocol === "https:") ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${wsHost}/ws/events`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
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

            if (msg.type === "hitl:new") {
              toast({
                title: "New HITL item",
                description: msg.data?.title ?? "Requires your attention",
              });
            }
          }

          if (msg.type === "agent:heartbeat") {
            queryClient.invalidateQueries({
              queryKey: ["agent-status"],
            });
            if (msg.data?.action) {
              toast({
                title: `Agent ${msg.data.action}`,
                description: `${msg.data.agent ?? "Unknown"} is now ${msg.data.status ?? msg.data.action}`,
              });
            }
          }

          if (msg.type === "project:update") {
            queryClient.invalidateQueries({ queryKey: ["jobs"] });
          }

          if (msg.type === "orch:status") {
            queryClient.invalidateQueries({ queryKey: ["orch-status"] });
            if (msg.data?.action) {
              toast({
                title: "Orchestrator",
                description: `Runner ${msg.data.action}`,
              });
            }
          }
          if (msg.type === "orch:goal") {
            queryClient.invalidateQueries({ queryKey: ["orch-goals"] });
            queryClient.invalidateQueries({ queryKey: ["orch-status"] });
          }
          if (msg.type === "orch:log") {
            queryClient.invalidateQueries({ queryKey: ["orch-logs"] });
          }

          if (msg.type === "notification") {
            toast({
              title: msg.data?.title ?? "Notification",
              description: msg.data?.message ?? "",
            });
          }
        } catch {
          // ignore non-JSON messages
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        reconnectTimeoutRef.current = setTimeout(connectWs, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
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
    .map((s) =>
      s
        .split("-")
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
        .join(" ")
    );

  const pendingCount = hitlData?.total ?? 0;
  const urgentCount = hitlData?.pending_urgent ?? 0;
  const pendingUsersCount = pendingUsersData?.total ?? 0;

  const userInitials = user?.name
    ? user.name
        .split(" ")
        .map((w) => w[0])
        .join("")
        .toUpperCase()
        .slice(0, 2)
    : user?.email?.slice(0, 2).toUpperCase() ?? "U";

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside
        className={`flex flex-col border-r border-border/50 bg-card/30 transition-all ${
          sidebarCollapsed ? "w-16" : "w-64"
        }`}
      >
        {/* Logo */}
        <div className="flex h-14 items-center border-b border-border/50 px-4">
          <span className="text-xl font-bold tracking-tight text-primary">
            MAS
          </span>
          {!sidebarCollapsed && (
            <span className="text-xs font-medium text-muted-foreground ml-2 mt-0.5">
              Multi-Agent Service
            </span>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="ml-auto h-7 w-7 text-muted-foreground"
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          >
            <svg
              className={`h-4 w-4 transition-transform ${sidebarCollapsed ? "rotate-180" : ""}`}
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M11 17l-5-5 5-5" />
              <path d="M18 17l-5-5 5-5" />
            </svg>
          </Button>
        </div>

        {/* Navigation */}
        <SidebarNav
          pendingCount={pendingCount}
          urgentCount={urgentCount}
          collapsed={sidebarCollapsed}
          pendingUsersCount={pendingUsersCount}
          userRole={user?.role}
        />

        {/* User section at bottom */}
        <div className="border-t border-border/50 p-3">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-accent transition-colors">
                <Avatar className="h-8 w-8">
                  <AvatarFallback className="text-xs bg-primary/10 text-primary">
                    {userInitials}
                  </AvatarFallback>
                </Avatar>
                {!sidebarCollapsed && (
                  <div className="min-w-0 text-left">
                    <p className="truncate text-sm font-medium text-foreground">
                      {user?.name || user?.email || "User"}
                    </p>
                    {user?.name && (
                      <p className="truncate text-xs text-muted-foreground">
                        {user.email}
                      </p>
                    )}
                  </div>
                )}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel>
                {user?.name || user?.email}
                {user?.role && (
                  <span className="text-xs font-normal text-muted-foreground block">
                    {user.role}
                  </span>
                )}
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => navigate("/settings")}>
                <svg
                  className="mr-2 h-4 w-4"
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                  <circle cx="12" cy="12" r="3" />
                </svg>
                Settings
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleLogout}>
                <svg
                  className="mr-2 h-4 w-4"
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4" />
                  <polyline points="16 17 21 12 16 7" />
                  <line x1="21" y1="12" x2="9" y2="12" />
                </svg>
                Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex h-14 items-center justify-between border-b border-border/50 px-6">
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

          <div className="flex items-center gap-1">
            {/* Theme toggle */}
            <button
              onClick={toggleTheme}
              className="p-2 rounded-md hover:bg-accent transition-colors"
              title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            >
              {theme === "dark" ? (
                <svg className="h-5 w-5 text-muted-foreground" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="5" />
                  <line x1="12" y1="1" x2="12" y2="3" />
                  <line x1="12" y1="21" x2="12" y2="23" />
                  <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                  <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                  <line x1="1" y1="12" x2="3" y2="12" />
                  <line x1="21" y1="12" x2="23" y2="12" />
                  <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
                  <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
                </svg>
              ) : (
                <svg className="h-5 w-5 text-muted-foreground" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
                </svg>
              )}
            </button>

            {/* Notification bell */}
            {pendingCount > 0 && (
              <button
                onClick={() => navigate("/hitl")}
                className="relative p-2 rounded-md hover:bg-accent transition-colors"
              >
                <svg
                  className="h-5 w-5 text-muted-foreground"
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9" />
                  <path d="M13.73 21a2 2 0 01-3.46 0" />
                </svg>
                <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-bold text-destructive-foreground">
                  {pendingCount}
                </span>
              </button>
            )}
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
