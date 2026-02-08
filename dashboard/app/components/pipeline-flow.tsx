import { cn } from "~/lib/utils";
import type { AgentStatus } from "~/lib/types";

const pipelineANodes = [
  "scout", "bid", "planner", "dev", "content", "design", "critic", "packager",
] as const;

const pipelineBNodes = ["geo_scout", "outreach"] as const;

const agentLabels: Record<string, string> = {
  scout: "Scout",
  bid: "Bid",
  planner: "Planner",
  dev: "Dev",
  content: "Content",
  design: "Design",
  critic: "Critic",
  packager: "Packager",
  geo_scout: "Geo Scout",
  outreach: "Outreach",
};

const statusColors: Record<string, string> = {
  idle: "bg-emerald-400/20 border-emerald-400/40 text-emerald-400",
  working: "bg-primary/20 border-primary/40 text-primary",
  error: "bg-destructive/20 border-destructive/40 text-destructive",
  dead: "bg-destructive/20 border-destructive/40 text-destructive",
  paused: "bg-muted border-muted-foreground/30 text-muted-foreground",
};

const statusDots: Record<string, string> = {
  idle: "bg-emerald-400",
  working: "bg-primary animate-pulse-dot",
  error: "bg-destructive",
  dead: "bg-destructive animate-pulse-dot",
  paused: "bg-muted-foreground",
};

interface PipelineFlowProps {
  agents: AgentStatus[];
}

export function PipelineFlow({ agents }: PipelineFlowProps) {
  const agentMap = new Map(agents.map((a) => [a.name, a]));

  return (
    <div className="space-y-6">
      <PipelineRow
        label="Pipeline A — Freelance"
        nodes={pipelineANodes as unknown as string[]}
        agentMap={agentMap}
      />
      <PipelineRow
        label="Pipeline B — Outreach"
        nodes={pipelineBNodes as unknown as string[]}
        agentMap={agentMap}
      />
    </div>
  );
}

function PipelineRow({
  label,
  nodes,
  agentMap,
}: {
  label: string;
  nodes: string[];
  agentMap: Map<string, AgentStatus>;
}) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground mb-3">{label}</p>
      <div className="flex items-center gap-1 overflow-x-auto pb-2">
        {nodes.map((name, i) => {
          const agent = agentMap.get(name);
          const status = agent?.status ?? "paused";
          return (
            <div key={name} className="flex items-center">
              <div
                className={cn(
                  "flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium whitespace-nowrap transition-all",
                  statusColors[status] ?? statusColors.paused
                )}
              >
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full shrink-0",
                    statusDots[status] ?? statusDots.paused
                  )}
                />
                {agentLabels[name] ?? name}
              </div>
              {i < nodes.length - 1 && (
                <svg
                  className="h-3 w-5 text-border shrink-0"
                  viewBox="0 0 20 12"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                >
                  <path d="M0 6h14M12 2l4 4-4 4" />
                </svg>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
