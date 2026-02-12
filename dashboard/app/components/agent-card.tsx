import { useState, memo } from "react";
import { Link } from "@remix-run/react";
import { useQueryClient } from "@tanstack/react-query";
import { Card, CardContent } from "~/components/ui/card";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import { StatusBadge } from "~/components/status-badge";
import { restartAgent, pauseAgent, resumeAgent } from "~/lib/api";
import { toast } from "~/hooks/use-toast";
import type { AgentStatus } from "~/lib/types";
import { relativeTime, cn } from "~/lib/utils";

interface AgentCardProps {
  agent: AgentStatus;
}

export const AgentCard = memo(function AgentCard({ agent }: AgentCardProps) {
  const queryClient = useQueryClient();
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const isWorking = agent.status === "working";
  const isError = agent.status === "error" || agent.status === "dead";
  const isPaused = agent.status === "paused";
  const isDead = agent.status === "dead";

  const handleAction = async (
    action: "restart" | "pause" | "resume",
    e: React.MouseEvent
  ) => {
    e.preventDefault();
    e.stopPropagation();
    setActionLoading(action);
    try {
      const fn =
        action === "restart"
          ? restartAgent
          : action === "pause"
          ? pauseAgent
          : resumeAgent;
      const result = await fn(agent.name);
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
            <div className="flex items-center gap-1.5">
              {agent.pipeline && (
                <Badge variant="outline" className="text-[10px] h-5 px-1.5">
                  Pipeline {agent.pipeline}
                </Badge>
              )}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 text-muted-foreground hover:text-foreground"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                    }}
                  >
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="h-4 w-4"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <circle cx="12" cy="12" r="1" />
                      <circle cx="12" cy="5" r="1" />
                      <circle cx="12" cy="19" r="1" />
                    </svg>
                    <span className="sr-only">Actions</span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="end"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                  }}
                >
                  <DropdownMenuItem
                    disabled={actionLoading !== null}
                    onClick={(e) => handleAction("restart", e)}
                  >
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="mr-2 h-4 w-4"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
                      <path d="M3 3v5h5" />
                      <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
                      <path d="M16 16h5v5" />
                    </svg>
                    {actionLoading === "restart" ? "Restarting..." : "Restart"}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  {isPaused ? (
                    <DropdownMenuItem
                      disabled={actionLoading !== null}
                      onClick={(e) => handleAction("resume", e)}
                    >
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="mr-2 h-4 w-4"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <polygon points="5 3 19 12 5 21 5 3" />
                      </svg>
                      {actionLoading === "resume" ? "Resuming..." : "Resume"}
                    </DropdownMenuItem>
                  ) : (
                    <DropdownMenuItem
                      disabled={actionLoading !== null || isDead}
                      onClick={(e) => handleAction("pause", e)}
                    >
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="mr-2 h-4 w-4"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <rect x="6" y="4" width="4" height="16" />
                        <rect x="14" y="4" width="4" height="16" />
                      </svg>
                      {actionLoading === "pause" ? "Pausing..." : "Pause"}
                    </DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
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
});
