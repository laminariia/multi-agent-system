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

const levelColors: Record<string, string> = {
  info: "text-primary",
  warning: "text-amber-400",
  error: "text-destructive",
  debug: "text-muted-foreground",
};

const levelBg: Record<string, string> = {
  info: "bg-primary/5",
  warning: "bg-amber-400/5",
  error: "bg-destructive/5",
  debug: "bg-muted/5",
};

export default function AgentDetailPage() {
  const { name } = useParams<{ name: string }>();
  const queryClient = useQueryClient();
  const [logLevel, setLogLevel] = useState("all");
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

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

    // Filter by log level
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
        <div className="flex items-start justify-between">
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold tracking-tight">
                {agent.display_name || agent.name}
              </h1>
              <StatusBadge status={agent.status} />
              {agent.pipeline && (
                <Badge variant="outline">Pipeline {agent.pipeline}</Badge>
              )}
              {/* Live Indicator */}
              <LiveIndicator isConnected={isConnected} />
            </div>
            <div className="flex items-center gap-4 text-sm text-muted-foreground">
              {agent.last_heartbeat && (
                <span>Last heartbeat: {relativeTime(agent.last_heartbeat)}</span>
              )}
              {agent.restart_count > 0 && (
                <span>{agent.restart_count} restarts</span>
              )}
            </div>
            {agent.current_task && (
              <p className="text-sm text-muted-foreground">
                Current task: {agent.current_task}
              </p>
            )}
            {agent.error_message && (
              <p className="text-sm text-destructive">
                Error: {agent.error_message}
              </p>
            )}
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
          <div className="rounded-full bg-muted p-4 mb-4">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-8 w-8 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
          </div>
          <p className="text-foreground text-lg font-medium">Agent not found</p>
          <p className="text-muted-foreground text-sm mt-1">
            No agent named &ldquo;{name}&rdquo; is registered
          </p>
        </div>
      ) : (
        <Skeleton className="h-[100px]" />
      )}

      {/* Metrics cards */}
      {agent && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card className="border-border/50">
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground">Status</p>
              <div className="mt-1">
                <StatusBadge status={agent.status} />
              </div>
            </CardContent>
          </Card>
          <Card className="border-border/50">
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground">Restarts</p>
              <p className="text-2xl font-bold mt-1">{agent.restart_count}</p>
            </CardContent>
          </Card>
          <Card className="border-border/50">
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground">Last Heartbeat</p>
              <p className="text-sm font-medium mt-1">
                {agent.last_heartbeat ? relativeTime(agent.last_heartbeat) : "Never"}
              </p>
            </CardContent>
          </Card>
          <Card className="border-border/50">
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground">Log Entries</p>
              <p className="text-2xl font-bold mt-1">{allLogs.length}</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Current task */}
      {agent?.current_task && (
        <Card className="border-primary/20 bg-primary/5">
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground mb-1">Current Task</p>
            <p className="text-sm font-medium">{agent.current_task}</p>
          </CardContent>
        </Card>
      )}

      {/* Error message */}
      {agent?.error_message && (
        <Card className="border-destructive/30 bg-destructive/5">
          <CardContent className="p-4">
            <p className="text-xs text-destructive mb-1">Last Error</p>
            <p className="text-sm">{agent.error_message}</p>
          </CardContent>
        </Card>
      )}

      {/* Logs */}
      <Card className="border-border/50">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <CardTitle className="text-base font-medium">Logs</CardTitle>
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
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="mr-1.5 h-3.5 w-3.5"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M3 6h18" />
                  <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
                  <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
                </svg>
                Clear
              </Button>
              <Tabs value={logLevel} onValueChange={setLogLevel}>
                <TabsList>
                  <TabsTrigger value="all">All</TabsTrigger>
                  <TabsTrigger value="info">Info</TabsTrigger>
                  <TabsTrigger value="warning">Warning</TabsTrigger>
                  <TabsTrigger value="error">Error</TabsTrigger>
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
              <ScrollArea className="h-[400px] rounded-md border border-border/50 bg-background/50">
                <div className="p-2 font-mono text-xs space-y-0.5">
                  {allLogs.map((log) => {
                    const ts = new Date(log.timestamp);
                    const time = ts.toLocaleTimeString("en-US", { hour12: false });
                    const isNew = "isNew" in log && log.isNew;
                    return (
                      <div
                        key={log.id}
                        className={cn(
                          "group flex gap-2 rounded px-2 py-1 hover:bg-accent/30 transition-all",
                          levelBg[log.level] ?? "",
                          isNew && "animate-in fade-in-50 bg-primary/10"
                        )}
                      >
                        <span className="text-muted-foreground shrink-0 w-[60px]">
                          {time}
                        </span>
                        <span
                          className={cn(
                            "shrink-0 w-[52px] font-medium uppercase",
                            levelColors[log.level] ?? "text-muted-foreground"
                          )}
                        >
                          {log.level}
                        </span>
                        <span className="text-muted-foreground shrink-0">
                          [{log.event_type}]
                        </span>
                        <span className="text-foreground/90 break-all flex-1">
                          {log.message || "\u2014"}
                        </span>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              type="button"
                              className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0 text-muted-foreground hover:text-foreground"
                              onClick={() => copyLogLine(log)}
                            >
                              {copiedId === log.id ? (
                                <svg
                                  xmlns="http://www.w3.org/2000/svg"
                                  className="h-3.5 w-3.5 text-success"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                >
                                  <polyline points="20 6 9 17 4 12" />
                                </svg>
                              ) : (
                                <svg
                                  xmlns="http://www.w3.org/2000/svg"
                                  className="h-3.5 w-3.5"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                >
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
          isConnected
            ? "bg-emerald-400 animate-pulse"
            : "bg-muted-foreground/50"
        )}
      />
      <span
        className={cn(
          "font-medium",
          textSize,
          isConnected ? "text-emerald-400" : "text-muted-foreground/50"
        )}
      >
        {isConnected ? "Live" : "Offline"}
      </span>
    </div>
  );
}
