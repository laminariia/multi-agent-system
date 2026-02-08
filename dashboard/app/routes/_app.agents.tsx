import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { StatusBadge } from "~/components/status-badge";
import { fetchAgentStatus } from "~/lib/api";
import { relativeTime } from "~/lib/utils";
import { cn } from "~/lib/utils";

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
  const { data, isLoading, error } = useQuery({
    queryKey: ["agent-status"],
    queryFn: fetchAgentStatus,
    refetchInterval: 10_000,
  });

  const health = data?.system_health
    ? healthConfig[data.system_health] ?? healthConfig.critical
    : null;

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

      {/* Loading state */}
      {isLoading && (
        <div className="flex items-center justify-center py-12">
          <svg
            className="h-6 w-6 animate-spin text-primary"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load agent status:{" "}
          {error instanceof Error ? error.message : "Unknown error"}
        </div>
      )}

      {/* Agents table */}
      {data && data.agents.length > 0 && (
        <Card className="border-slate-700/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium">Agents</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-slate-700/50">
                    <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                      Pipeline
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                      Agent
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                      Status
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                      Current Task
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                      Last Heartbeat
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-muted-foreground uppercase tracking-wider">
                      Restarts
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/30">
                  {data.agents.map((agent) => (
                    <tr
                      key={agent.name}
                      className="hover:bg-accent/50 transition-colors"
                    >
                      <td className="px-6 py-3.5 text-sm">
                        {agent.pipeline ? (
                          <span className="inline-flex items-center justify-center h-6 w-6 rounded bg-slate-700/50 text-xs font-medium text-muted-foreground">
                            {agent.pipeline}
                          </span>
                        ) : (
                          <span className="text-muted-foreground/50">--</span>
                        )}
                      </td>
                      <td className="px-6 py-3.5">
                        <div>
                          <p className="text-sm font-medium text-foreground">
                            {agent.display_name || agent.name}
                          </p>
                          {agent.display_name && (
                            <p className="text-xs text-muted-foreground">
                              {agent.name}
                            </p>
                          )}
                        </div>
                      </td>
                      <td className="px-6 py-3.5">
                        <StatusBadge status={agent.status} />
                        {agent.error_message && (
                          <p className="mt-1 text-xs text-destructive line-clamp-1">
                            {agent.error_message}
                          </p>
                        )}
                      </td>
                      <td className="px-6 py-3.5 text-sm text-muted-foreground max-w-[200px]">
                        {agent.current_task ? (
                          <span className="line-clamp-1">{agent.current_task}</span>
                        ) : (
                          <span className="text-muted-foreground/40">--</span>
                        )}
                      </td>
                      <td className="px-6 py-3.5 text-sm text-muted-foreground">
                        {agent.last_heartbeat ? (
                          relativeTime(agent.last_heartbeat)
                        ) : (
                          <span className="text-muted-foreground/40">Never</span>
                        )}
                      </td>
                      <td className="px-6 py-3.5 text-sm text-right">
                        <span
                          className={cn(
                            agent.restart_count > 3
                              ? "text-destructive"
                              : agent.restart_count > 0
                              ? "text-amber-400"
                              : "text-muted-foreground"
                          )}
                        >
                          {agent.restart_count}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Empty state */}
      {data && data.agents.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
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
