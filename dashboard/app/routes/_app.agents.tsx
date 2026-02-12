import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent } from "~/components/ui/card";
import { Skeleton } from "~/components/ui/skeleton";
import { Button } from "~/components/ui/button";
import { AgentCard } from "~/components/agent-card";
import { fetchAgentStatus } from "~/lib/api";
import { relativeTime, cn } from "~/lib/utils";

const healthConfig: Record<string, { bg: string; border: string; text: string; label: string }> = {
  healthy: {
    bg: "bg-emerald-500/10",
    border: "border-emerald-500/30",
    text: "text-emerald-400",
    label: "All Systems Operational",
  },
  degraded: {
    bg: "bg-amber-500/10",
    border: "border-amber-500/30",
    text: "text-amber-400",
    label: "Degraded Performance",
  },
  critical: {
    bg: "bg-red-500/10",
    border: "border-red-500/30",
    text: "text-red-400",
    label: "Critical Issues Detected",
  },
};

export default function AgentsPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["agent-status"],
    queryFn: fetchAgentStatus,
    refetchInterval: 30_000,
  });

  const health = data?.system_health
    ? healthConfig[data.system_health] ?? healthConfig.critical
    : null;

  const pipelineA = data?.agents.filter((a) => a.pipeline === "A") ?? [];
  const pipelineB = data?.agents.filter((a) => a.pipeline === "B") ?? [];
  const other = data?.agents.filter((a) => !a.pipeline) ?? [];

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">
          Agent Status
        </h1>
        {data?.last_check && (
          <span className="text-sm text-muted-foreground">
            Last check: {relativeTime(data.last_check)}
          </span>
        )}
      </div>

      {/* System health banner */}
      {health && (
        <Card className={cn("border", health.border, health.bg)}>
          <CardContent className="flex items-center gap-3 py-4">
            <div
              className={cn(
                "h-2.5 w-2.5 rounded-full",
                data?.system_health === "healthy"
                  ? "bg-emerald-400"
                  : data?.system_health === "degraded"
                  ? "bg-amber-400"
                  : "bg-red-400 animate-pulse-dot"
              )}
            />
            <span className={cn("font-medium", health.text)}>
              {health.label}
            </span>
            {data && (
              <span className="text-sm text-muted-foreground ml-auto">
                {data.agents.filter((a) => a.status === "working").length} working
                {" / "}
                {data.agents.length} total
              </span>
            )}
          </CardContent>
        </Card>
      )}

      {/* Loading */}
      {isLoading && !data && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-[160px]" />
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

      {/* Agent cards by pipeline */}
      {data && data.agents.length > 0 && (
        <div className="space-y-8">
          {pipelineA.length > 0 && (
            <div>
              <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground/60 mb-4">
                Pipeline A — Freelance
              </h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {pipelineA.map((agent) => (
                  <AgentCard key={agent.name} agent={agent} />
                ))}
              </div>
            </div>
          )}

          {pipelineB.length > 0 && (
            <div>
              <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground/60 mb-4">
                Pipeline B — Outreach
              </h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {pipelineB.map((agent) => (
                  <AgentCard key={agent.name} agent={agent} />
                ))}
              </div>
            </div>
          )}

          {other.length > 0 && (
            <div>
              <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground/60 mb-4">
                Other
              </h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {other.map((agent) => (
                  <AgentCard key={agent.name} agent={agent} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Empty */}
      {data && data.agents.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-center animate-fade-in">
          <p className="text-muted-foreground text-lg font-medium">
            No agents registered
          </p>
          <p className="text-muted-foreground/70 text-sm mt-1">
            Agents will appear here once they start sending heartbeats
          </p>
        </div>
      )}
    </div>
  );
}
