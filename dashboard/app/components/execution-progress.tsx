import { cn } from "~/lib/utils";
import type { PipelineProgress } from "~/lib/types";

/** Human-readable agent display names. */
const AGENT_LABELS: Record<string, string> = {
  planner: "Planner",
  dev: "Developer",
  content: "Content",
  design: "Design",
  critic: "Critic",
  packager: "Packager",
};

function agentLabel(name: string): string {
  return AGENT_LABELS[name] || name.charAt(0).toUpperCase() + name.slice(1);
}

interface ExecutionProgressProps {
  progress: PipelineProgress;
  className?: string;
}

/**
 * Displays real-time pipeline execution progress with a progress bar,
 * current agent indicator, and step-by-step agent status.
 */
export function ExecutionProgress({
  progress,
  className,
}: ExecutionProgressProps) {
  if (progress.status === "idle") {
    return null;
  }

  const completedCount = progress.completed_agents.length;
  const total = progress.total_agents || 1;
  const pct = Math.min(Math.round((completedCount / total) * 100), 100);

  // Build the full ordered agent list: planner + sequence + critic + packager
  const allAgents = buildAgentList(progress.agent_sequence);
  const completedSet = new Set(progress.completed_agents);

  const statusColors: Record<string, string> = {
    running: "bg-primary",
    paused: "bg-yellow-500",
    completed: "bg-green-500",
    failed: "bg-destructive",
  };

  const statusLabels: Record<string, string> = {
    running: "Running",
    paused: "Paused (HITL)",
    completed: "Completed",
    failed: "Failed",
  };

  const barColor = statusColors[progress.status] || "bg-primary";

  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-4 space-y-3",
        className
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Pipeline Execution</h3>
        <span
          className={cn(
            "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
            progress.status === "running" &&
              "bg-primary/10 text-primary",
            progress.status === "paused" &&
              "bg-yellow-500/10 text-yellow-600",
            progress.status === "completed" &&
              "bg-green-500/10 text-green-600",
            progress.status === "failed" &&
              "bg-destructive/10 text-destructive"
          )}
        >
          {statusLabels[progress.status] || progress.status}
        </span>
      </div>

      {/* Progress bar */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>
            {completedCount} / {total} agents
          </span>
          <span>{pct}%</span>
        </div>
        <div className="h-2 w-full rounded-full bg-secondary">
          <div
            className={cn(
              "h-2 rounded-full transition-all duration-500",
              barColor
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* Agent steps */}
      <div className="flex flex-wrap gap-2">
        {allAgents.map((agent) => {
          const isCompleted = completedSet.has(agent);
          const isCurrent = progress.current_agent === agent;
          const isPending = !isCompleted && !isCurrent;

          return (
            <div
              key={agent}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2 py-1 text-xs",
                isCompleted && "bg-green-500/10 text-green-600",
                isCurrent && "bg-primary/10 text-primary font-medium",
                isPending && "bg-muted text-muted-foreground"
              )}
            >
              {/* Status icon */}
              {isCompleted && (
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="h-3 w-3"
                  viewBox="0 0 20 20"
                  fill="currentColor"
                >
                  <path
                    fillRule="evenodd"
                    d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                    clipRule="evenodd"
                  />
                </svg>
              )}
              {isCurrent && (
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-primary" />
                </span>
              )}
              {isPending && (
                <span className="h-2 w-2 rounded-full bg-muted-foreground/30" />
              )}
              {agentLabel(agent)}
            </div>
          );
        })}
      </div>

      {/* Elapsed time */}
      {progress.started_at && (
        <p className="text-xs text-muted-foreground">
          Started{" "}
          {new Date(progress.started_at).toLocaleTimeString()}
        </p>
      )}
    </div>
  );
}

/** Build the full ordered agent list from the dynamic sequence. */
function buildAgentList(sequence: string[]): string[] {
  if (sequence.length === 0) {
    return ["planner", "packager"];
  }
  return ["planner", ...sequence, "critic", "packager"];
}
