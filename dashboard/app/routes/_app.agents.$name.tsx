import { useState } from "react";
import { useParams, Link } from "@remix-run/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Separator } from "~/components/ui/separator";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { StatusBadge } from "~/components/status-badge";
import { LogViewer } from "~/components/log-viewer";
import { fetchAgentStatus, fetchAgentLogs, restartAgent, pauseAgent, resumeAgent } from "~/lib/api";
import { relativeTime } from "~/lib/utils";
import { toast } from "~/hooks/use-toast";
import { useWsSubscription } from "~/hooks/use-ws-subscription";

export default function AgentDetailPage() {
  const { name } = useParams<{ name: string }>();
  const queryClient = useQueryClient();
  const [logLevel, setLogLevel] = useState("all");
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Subscribe to agent-specific WebSocket channel for real-time log updates
  useWsSubscription("subscribe:agent", name);

  const { data: statusData } = useQuery({
    queryKey: ["agent-status"],
    queryFn: fetchAgentStatus,
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
    refetchInterval: 30_000,
  });

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
              <p className="text-2xl font-bold mt-1">{logsData?.total ?? 0}</p>
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
            <CardTitle className="text-base font-medium">Logs</CardTitle>
            <Tabs value={logLevel} onValueChange={setLogLevel}>
              <TabsList>
                <TabsTrigger value="all">All</TabsTrigger>
                <TabsTrigger value="info">Info</TabsTrigger>
                <TabsTrigger value="warning">Warning</TabsTrigger>
                <TabsTrigger value="error">Error</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        </CardHeader>
        <CardContent>
          {logsLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-6" />
              ))}
            </div>
          ) : (
            <LogViewer logs={logsData?.logs ?? []} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
