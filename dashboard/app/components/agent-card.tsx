import { Link } from "@remix-run/react";
import { Card, CardContent } from "~/components/ui/card";
import { Badge } from "~/components/ui/badge";
import { StatusBadge } from "~/components/status-badge";
import type { AgentStatus } from "~/lib/types";
import { relativeTime, cn } from "~/lib/utils";

interface AgentCardProps {
  agent: AgentStatus;
}

export function AgentCard({ agent }: AgentCardProps) {
  const isWorking = agent.status === "working";
  const isError = agent.status === "error" || agent.status === "dead";

  return (
    <Link to={`/agents/${agent.name}`} className="block">
      <Card
        className={cn(
          "border-border/50 transition-all hover:border-primary/30 hover:shadow-lg hover:shadow-primary/5 cursor-pointer animate-fade-in",
          isWorking && "border-primary/20 shadow-sm shadow-primary/5",
          isError && "border-destructive/20 shadow-sm shadow-destructive/5"
        )}
      >
        <CardContent className="p-4">
          <div className="flex items-start justify-between mb-3">
            <div className="flex items-center gap-2.5">
              <div
                className={cn(
                  "h-2.5 w-2.5 rounded-full",
                  agent.status === "idle" && "bg-emerald-400",
                  agent.status === "working" && "bg-primary animate-pulse-dot",
                  agent.status === "error" && "bg-destructive",
                  agent.status === "dead" && "bg-destructive animate-pulse-dot",
                  agent.status === "paused" && "bg-muted-foreground"
                )}
              />
              <span className="font-medium text-sm">
                {agent.display_name || agent.name}
              </span>
            </div>
            {agent.pipeline && (
              <Badge variant="outline" className="text-[10px] h-5 px-1.5">
                Pipeline {agent.pipeline}
              </Badge>
            )}
          </div>

          <div className="space-y-2">
            <StatusBadge status={agent.status} className="text-[10px]" />

            {agent.current_task && (
              <p className="text-xs text-muted-foreground line-clamp-1">
                {agent.current_task}
              </p>
            )}

            {agent.error_message && (
              <p className="text-xs text-destructive line-clamp-1">
                {agent.error_message}
              </p>
            )}

            <div className="flex items-center justify-between pt-1 border-t border-border/30">
              <span className="text-[10px] text-muted-foreground">
                {agent.last_heartbeat
                  ? relativeTime(agent.last_heartbeat)
                  : "No heartbeat"}
              </span>
              {agent.restart_count > 0 && (
                <span
                  className={cn(
                    "text-[10px]",
                    agent.restart_count > 3
                      ? "text-destructive"
                      : "text-muted-foreground"
                  )}
                >
                  {agent.restart_count} restarts
                </span>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
