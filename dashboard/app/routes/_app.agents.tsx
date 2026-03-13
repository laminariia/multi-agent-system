import { useQuery, useQueryClient } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Card, CardContent } from "~/components/ui/card";
import { Skeleton } from "~/components/ui/skeleton";
import { Button } from "~/components/ui/button";
import { Badge } from "~/components/ui/badge";
import { Link } from "@remix-run/react";
import { fetchAgentStatus, restartAgent, pauseAgent } from "~/lib/api";
import { relativeTime, cn } from "~/lib/utils";
import { toast } from "~/hooks/use-toast";
import type { AgentStatus } from "~/lib/types";

const healthConfig: Record<string, { bg: string; border: string; text: string; label: string; dot: string }> = {
  healthy: {
    bg: "bg-emerald-500/10",
    border: "border-emerald-500/30",
    text: "text-emerald-400",
    dot: "bg-emerald-400",
    label: "All Systems Operational",
  },
  degraded: {
    bg: "bg-amber-500/10",
    border: "border-amber-500/30",
    text: "text-amber-400",
    dot: "bg-amber-400",
    label: "Degraded Performance",
  },
  critical: {
    bg: "bg-red-500/10",
    border: "border-red-500/30",
    text: "text-red-400",
    dot: "bg-red-400 animate-pulse",
    label: "Critical Issues Detected",
  },
};

const statusBorderColor: Record<string, string> = {
  working: "border-l-emerald-500",
  idle: "border-l-border/50",
  error: "border-l-red-500",
  dead: "border-l-red-500",
  paused: "border-l-orange-500",
};

const statusBadgeConfig: Record<string, { label: string; className: string }> = {
  working: { label: "Working", className: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30" },
  idle: { label: "Idle", className: "bg-zinc-700/50 text-zinc-400 border-zinc-600/30" },
  error: { label: "Error", className: "bg-red-500/20 text-red-400 border-red-500/30" },
  dead: { label: "Dead", className: "bg-red-500/20 text-red-400 border-red-500/30" },
  paused: { label: "Paused", className: "bg-orange-500/20 text-orange-400 border-orange-500/30" },
};

const avatarColors: Record<string, string> = {
  working: "bg-emerald-500/20 text-emerald-400",
  idle: "bg-zinc-700/50 text-zinc-400",
  error: "bg-red-500/20 text-red-400",
  dead: "bg-red-500/20 text-red-400",
  paused: "bg-orange-500/20 text-orange-400",
};

function getInitials(name: string, displayName: string | null): string {
  const source = displayName ?? name;
  const words = source.replace(/_/g, " ").split(" ").filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return source.slice(0, 2).toUpperCase();
}

function AgentCardNew({ agent }: { agent: AgentStatus }) {
  const initials = getInitials(agent.name, agent.display_name);
  const cfg = statusBadgeConfig[agent.status] ?? statusBadgeConfig.idle;
  const avatarCfg = avatarColors[agent.status] ?? avatarColors.idle;
  const borderColor = statusBorderColor[agent.status] ?? "border-l-border/50";

  return (
    <Link to={`/agents/${agent.name}`} className="block">
      <Card
        className={cn(
          "border border-l-4 border-border/50 transition-all hover:border-primary/30 hover:shadow-lg hover:shadow-primary/5 cursor-pointer bg-zinc-900",
          borderColor
        )}
      >
        <CardContent className="p-4">
          <div className="flex items-start gap-3 mb-3">
            <div className={cn("h-9 w-9 rounded-full flex items-center justify-center text-xs font-bold shrink-0", avatarCfg)}>
              {initials}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold truncate">{agent.display_name || agent.name}</p>
              <Badge
                variant="outline"
                className={cn("text-[10px] h-4 px-1.5 mt-0.5 border", cfg.className)}
              >
                {cfg.label}
              </Badge>
            </div>
          </div>

          {(agent.current_task || agent.error_message) && (
            <p className={cn(
              "text-xs line-clamp-2 mb-2",
              agent.error_message ? "text-red-400" : "text-muted-foreground"
            )}>
              {agent.error_message ?? agent.current_task}
            </p>
          )}

          <div className="pt-2 border-t border-border/30">
            <span className="text-[10px] text-muted-foreground">
              {agent.last_heartbeat ? relativeTime(agent.last_heartbeat) : "No heartbeat"}
            </span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

export default function AgentsPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["agent-status"],
    queryFn: fetchAgentStatus,
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  const health = data?.system_health
    ? healthConfig[data.system_health] ?? healthConfig.critical
    : null;

  const pipelineA = data?.agents.filter((a) => a.pipeline === "A") ?? [];
  const pipelineB = data?.agents.filter((a) => a.pipeline === "B") ?? [];
  const system = data?.agents.filter((a) => !a.pipeline) ?? [];

  const workingCount = data?.agents.filter((a) => a.status === "working").length ?? 0;
  const totalCount = data?.agents.length ?? 0;

  const handleRestartAll = async () => {
    if (!data) return;
    for (const agent of data.agents) {
      try {
        await restartAgent(agent.name);
      } catch {
        // continue
      }
    }
    toast({ title: "All agents restarted", variant: "success" });
    queryClient.invalidateQueries({ queryKey: ["agent-status"] });
  };

  const handlePauseAll = async () => {
    if (!data) return;
    for (const agent of data.agents.filter((a) => a.status === "working")) {
      try {
        await pauseAgent(agent.name);
      } catch {
        // continue
      }
    }
    toast({ title: "All working agents paused", variant: "success" });
    queryClient.invalidateQueries({ queryKey: ["agent-status"] });
  };

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">Agent Status</h1>
          {health && (
            <div className="flex items-center gap-1.5">
              <div className={cn("h-2 w-2 rounded-full", health.dot)} />
              <span className={cn("text-sm font-medium", health.text)}>{health.label}</span>
            </div>
          )}
          {data && (
            <span className="text-xs text-muted-foreground">
              Uptime {workingCount}/{totalCount} active
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={handlePauseAll}
            disabled={!data || workingCount === 0}
          >
            Pause All
          </Button>
          <Button
            size="sm"
            className="bg-orange-500 hover:bg-orange-600 text-white"
            onClick={handleRestartAll}
            disabled={!data}
          >
            Restart All
          </Button>
        </div>
      </div>

      {/* Loading */}
      {isLoading && !data && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 9 }).map((_, i) => (
            <Skeleton key={i} className="h-[140px]" />
          ))}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="flex items-center justify-between rounded-lg border border-destructive/50 bg-destructive/10 p-4">
          <p className="text-sm text-destructive">
            Failed to load agent status:{" "}
            {error instanceof Error ? error.message : "Unknown error"}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => queryClient.invalidateQueries({ queryKey: ["agent-status"] })}
          >
            Retry
          </Button>
        </div>
      )}

      {/* Agent cards by section */}
      {data && data.agents.length > 0 && (
        <div className="space-y-8">
          {pipelineA.length > 0 && (
            <div>
              <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground/60 mb-4">
                Pipeline A
              </h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {pipelineA.map((agent) => (
                  <AgentCardNew key={agent.name} agent={agent} />
                ))}
              </div>
            </div>
          )}

          {pipelineB.length > 0 && (
            <div>
              <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground/60 mb-4">
                Pipeline B
              </h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {pipelineB.map((agent) => (
                  <AgentCardNew key={agent.name} agent={agent} />
                ))}
              </div>
            </div>
          )}

          {system.length > 0 && (
            <div>
              <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground/60 mb-4">
                System
              </h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {system.map((agent) => (
                  <AgentCardNew key={agent.name} agent={agent} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Empty */}
      {data && data.agents.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <p className="text-muted-foreground text-lg font-medium">No agents registered</p>
          <p className="text-muted-foreground/70 text-sm mt-1">
            Agents will appear here once they start sending heartbeats
          </p>
        </div>
      )}
    </div>
  );
}
