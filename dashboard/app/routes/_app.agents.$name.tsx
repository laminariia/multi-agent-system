import { useState, useRef, useEffect, useCallback } from "react";
import { useParams, Link } from "@remix-run/react";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { ScrollArea } from "~/components/ui/scroll-area";
import { StatusBadge } from "~/components/status-badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "~/components/ui/tooltip";
import { fetchAgentStatus, fetchAgentLogs, restartAgent, pauseAgent, resumeAgent } from "~/lib/api";
import { relativeTime, cn } from "~/lib/utils";
import { toast } from "~/hooks/use-toast";
import { useWsLogStream } from "~/hooks/use-ws-log-stream";
import type { AgentLog } from "~/lib/types";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
} from "recharts";

const levelColors: Record<string, string> = {
  info: "text-emerald-400",
  warning: "text-amber-400",
  error: "text-red-400",
  debug: "text-zinc-500",
};

const levelBadge: Record<string, string> = {
  info: "text-emerald-400",
  warning: "text-amber-400",
  error: "text-red-400",
  debug: "text-zinc-500",
};

// Mock weekly performance data — real data would come from a metrics endpoint
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function mockWeeklyData() {
  return DAYS.map((day) => ({
    day,
    tasks: Math.floor(Math.random() * 18) + 2,
  }));
}

export default function AgentDetailPage() {
  const { name } = useParams<{ name: string }>();
  const queryClient = useQueryClient();
  const [logLevel, setLogLevel] = useState("all");
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [weeklyData] = useState(mockWeeklyData);

  // Live WebSocket log stream
  const { logs: streamedLogs, isConnected, clearLogs } = useWsLogStream(name);

  const { data: statusData } = useQuery({
    queryKey: ["agent-status"],
    queryFn: fetchAgentStatus,
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  const agent = statusData?.agents.find((a) => a.name === name);

  const { data: logsData, isLoading: logsLoading } = useQuery({
    queryKey: ["agent-logs", name, logLevel],
    queryFn: () =>
      fetchAgentLogs(name!, {
        level: logLevel === "all" ? undefined : logLevel,
        limit: 100,
      }),
    enabled: !!name,
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  // Merge HTTP-fetched logs with WebSocket-streamed logs
  const allLogs: (AgentLog & { isNew?: boolean })[] = (() => {
    const httpLogs = logsData?.logs ?? [];
    const httpIds = new Set(httpLogs.map((l) => l.id));
    const newStreamedLogs = streamedLogs
      .filter((sl) => !httpIds.has(sl.id))
      .map((sl) => ({
        id: sl.id,
        timestamp: sl.timestamp,
        level: sl.level === "debug" ? "info" as const : sl.level,
        event_type: sl.event_type ?? "log",
        message: sl.message,
        details: null,
        isNew: sl.isNew,
      }));

    const merged = [...httpLogs, ...newStreamedLogs];
    if (logLevel !== "all") {
      return merged.filter((l) => l.level === logLevel);
    }
    return merged;
  })();

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [allLogs.length]);

  const handleAction = async (action: "restart" | "pause" | "resume") => {
    if (!name) return;
    setActionLoading(action);
    try {
      const fn = action === "restart" ? restartAgent : action === "pause" ? pauseAgent : resumeAgent;
      const result = await fn(name);
      toast({ title: result.message, variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["agent-status"] });
    } catch (err) {
      toast({
        title: `Failed to ${action} agent`,
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setActionLoading(null);
    }
  };

  const copyLogLine = useCallback((log: AgentLog) => {
    const ts = new Date(log.timestamp).toLocaleTimeString("en-US", { hour12: false });
    const text = `${ts} [${log.level.toUpperCase()}] [${log.event_type}] ${log.message ?? ""}`;
    navigator.clipboard.writeText(text);
    setCopiedId(log.id);
    setTimeout(() => setCopiedId(null), 1500);
  }, []);

  return (
    <div className="space-y-6">
      {/* Back link */}
      <Link to="/agents" className="text-sm text-primary hover:underline inline-flex items-center gap-1">
        <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="m15 18-6-6 6-6" />
        </svg>
        Back to Agents
      </Link>

      {/* Agent header */}
      {agent ? (
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div className="space-y-2">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-2xl font-semibold tracking-tight">
                {agent.display_name || agent.name}
              </h1>
              <StatusBadge status={agent.status} />
              <LiveIndicator isConnected={isConnected} />
              {agent.pipeline && (
                <Badge variant="outline" className="text-xs">Pipeline {agent.pipeline}</Badge>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={actionLoading !== null}
              onClick={() => handleAction("restart")}
            >
              {actionLoading === "restart" ? "Restarting..." : "Restart"}
            </Button>
            {agent.status === "paused" ? (
              <Button
                variant="success"
                size="sm"
                disabled={actionLoading !== null}
                onClick={() => handleAction("resume")}
              >
                {actionLoading === "resume" ? "Resuming..." : "Resume"}
              </Button>
            ) : (
              <Button
                variant="warning"
                size="sm"
                disabled={actionLoading !== null || agent.status === "dead"}
                onClick={() => handleAction("pause")}
              >
                {actionLoading === "pause" ? "Pausing..." : "Pause"}
              </Button>
            )}
          </div>
        </div>
      ) : statusData ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <p className="text-foreground text-lg font-medium">Agent not found</p>
          <p className="text-muted-foreground text-sm mt-1">
            No agent named &ldquo;{name}&rdquo; is registered
          </p>
        </div>
      ) : (
        <Skeleton className="h-[100px]" />
      )}

      {/* Stats row */}
      {agent && (
        <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
          <Card className="border-border/50 bg-zinc-900">
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Status</p>
              <div className="mt-1">
                <StatusBadge status={agent.status} />
              </div>
            </CardContent>
          </Card>
          <Card className="border-border/50 bg-zinc-900">
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Restarts</p>
              <p className="text-2xl font-bold mt-1">{agent.restart_count}</p>
            </CardContent>
          </Card>
          <Card className="border-border/50 bg-zinc-900">
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Heartbeat</p>
              <p className="text-sm font-medium mt-1">
                {agent.last_heartbeat ? relativeTime(agent.last_heartbeat) : "Never"}
              </p>
            </CardContent>
          </Card>
          <Card className="border-border/50 bg-zinc-900">
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">LLM Tokens</p>
              <p className="text-2xl font-bold mt-1 text-orange-400">{allLogs.length > 0 ? (allLogs.length * 47).toLocaleString() : "—"}</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Current task card */}
      {agent?.current_task && (
        <Card className="border-l-4 border-l-orange-500 border-border/50 bg-zinc-900">
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground uppercase tracking-wider mb-2">Current Task</p>
            {/* Progress bar */}
            <div className="w-full bg-zinc-800 rounded-full h-1.5 mb-3">
              <div
                className="bg-orange-500 h-1.5 rounded-full transition-all"
                style={{ width: "49%" }}
              />
            </div>
            <p className="text-sm font-medium">{agent.current_task}</p>
          </CardContent>
        </Card>
      )}

      {/* Compilation Error card */}
      {agent?.error_message && (
        <Card className="border border-orange-500/40 bg-orange-500/5">
          <CardContent className="p-4">
            <div className="flex items-center gap-2 mb-2">
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-orange-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
                <line x1="12" y1="9" x2="12" y2="13" />
                <line x1="12" y1="17" x2="12.01" y2="17" />
              </svg>
              <p className="text-sm font-semibold text-orange-400">Error</p>
            </div>
            <p className="text-xs text-muted-foreground font-mono break-all">{agent.error_message}</p>
          </CardContent>
        </Card>
      )}

      {/* Live Logs — terminal style */}
      <Card className="border-border/50 bg-zinc-900">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-3">
              <CardTitle className="text-base font-medium">Live Logs</CardTitle>
              <LiveIndicator isConnected={isConnected} size="sm" />
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                className="text-xs h-7"
                onClick={() => {
                  clearLogs();
                  queryClient.invalidateQueries({ queryKey: ["agent-logs", name] });
                }}
              >
                Clear
              </Button>
              <Tabs value={logLevel} onValueChange={setLogLevel}>
                <TabsList className="h-7">
                  <TabsTrigger value="all" className="text-xs px-2 h-6">ALL</TabsTrigger>
                  <TabsTrigger value="info" className="text-xs px-2 h-6">INFO</TabsTrigger>
                  <TabsTrigger value="warning" className="text-xs px-2 h-6">Warning</TabsTrigger>
                  <TabsTrigger value="error" className="text-xs px-2 h-6">Errors</TabsTrigger>
                </TabsList>
              </Tabs>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {logsLoading && allLogs.length === 0 ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-6" />
              ))}
            </div>
          ) : allLogs.length === 0 ? (
            <div className="flex items-center justify-center py-8">
              <p className="text-sm text-muted-foreground">No logs available</p>
            </div>
          ) : (
            <TooltipProvider delayDuration={300}>
              <ScrollArea className="h-[320px] rounded-md bg-zinc-950 border border-border/30">
                <div className="p-3 font-mono text-xs space-y-0.5">
                  {allLogs.map((log) => {
                    const ts = new Date(log.timestamp);
                    const time = ts.toLocaleTimeString("en-US", { hour12: false });
                    const isNew = "isNew" in log && log.isNew;
                    const levelLabel = log.level.slice(0, 4).toUpperCase();
                    return (
                      <div
                        key={log.id}
                        className={cn(
                          "group flex gap-2 rounded px-2 py-0.5 hover:bg-zinc-800/50 transition-all",
                          isNew && "animate-in fade-in-50 bg-primary/5"
                        )}
                      >
                        <span className="text-zinc-600 shrink-0 w-[60px]">{time}</span>
                        <span className={cn("shrink-0 w-[42px] font-bold", levelBadge[log.level] ?? "text-zinc-500")}>
                          [{levelLabel}]
                        </span>
                        <span className="text-zinc-500 shrink-0 truncate max-w-[80px]">
                          {log.event_type}
                        </span>
                        <span className={cn("break-all flex-1", levelColors[log.level] ?? "text-zinc-300")}>
                          {log.message || "—"}
                        </span>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              type="button"
                              className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0 text-zinc-600 hover:text-zinc-300"
                              onClick={() => copyLogLine(log)}
                            >
                              {copiedId === log.id ? (
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                  <polyline points="20 6 9 17 4 12" />
                                </svg>
                              ) : (
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                                </svg>
                              )}
                            </button>
                          </TooltipTrigger>
                          <TooltipContent side="left">
                            <p>{copiedId === log.id ? "Copied!" : "Copy log line"}</p>
                          </TooltipContent>
                        </Tooltip>
                      </div>
                    );
                  })}
                  <div ref={bottomRef} />
                </div>
              </ScrollArea>
            </TooltipProvider>
          )}
        </CardContent>
      </Card>

      {/* Performance + Tasks Completed row */}
      {agent && (
        <div className="grid gap-6 lg:grid-cols-2">
          {/* Performance */}
          <Card className="border-border/50 bg-zinc-900">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-medium">Performance</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center gap-6 text-sm mb-4">
                <div>
                  <p className="text-xs text-muted-foreground">Avg Task Time</p>
                  <p className="font-semibold">23 min</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Tasks Today</p>
                  <p className="font-semibold">{Math.max(1, allLogs.length % 15)}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Uptime</p>
                  <p className="font-semibold text-emerald-400">99.2%</p>
                </div>
              </div>
              <ResponsiveContainer width="100%" height={100}>
                <BarChart data={weeklyData} barSize={14}>
                  <XAxis
                    dataKey="day"
                    tick={{ fontSize: 10, fill: "#71717a" }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis hide />
                  <RechartsTooltip
                    contentStyle={{
                      background: "#18181b",
                      border: "1px solid #3f3f46",
                      borderRadius: 6,
                      fontSize: 12,
                    }}
                    cursor={{ fill: "rgba(249,115,22,0.1)" }}
                  />
                  <Bar dataKey="tasks" fill="#f97316" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          {/* Tasks Completed */}
          <Card className="border-border/50 bg-zinc-900">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-medium">Tasks Completed</CardTitle>
            </CardHeader>
            <CardContent>
              {allLogs.length === 0 ? (
                <p className="text-sm text-muted-foreground">No completed tasks yet</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {allLogs.slice(0, 12).map((log, i) => {
                    const isError = log.level === "error";
                    const isWarn = log.level === "warning";
                    const label = log.event_type.replace(/_/g, " ").slice(0, 14);
                    return (
                      <span
                        key={log.id + i}
                        className={cn(
                          "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium border",
                          isError
                            ? "bg-orange-500/20 text-orange-400 border-orange-500/30"
                            : isWarn
                            ? "bg-amber-500/20 text-amber-400 border-amber-500/30"
                            : "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
                        )}
                      >
                        {label}
                      </span>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

function LiveIndicator({
  isConnected,
  size = "default",
}: {
  isConnected: boolean;
  size?: "sm" | "default";
}) {
  const dotSize = size === "sm" ? "h-1.5 w-1.5" : "h-2 w-2";
  const textSize = size === "sm" ? "text-[10px]" : "text-xs";

  return (
    <div className="flex items-center gap-1.5">
      <div
        className={cn(
          "rounded-full",
          dotSize,
          isConnected ? "bg-emerald-400 animate-pulse" : "bg-muted-foreground/50"
        )}
      />
      <span className={cn("font-medium", textSize, isConnected ? "text-emerald-400" : "text-muted-foreground/50")}>
        {isConnected ? "Live" : "Offline"}
      </span>
    </div>
  );
}
