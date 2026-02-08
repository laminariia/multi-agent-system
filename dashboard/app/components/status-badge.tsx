import { Badge } from "~/components/ui/badge";
import { cn } from "~/lib/utils";

type AgentStatusType = "idle" | "working" | "error" | "dead" | "paused";

const statusConfig: Record<
  AgentStatusType,
  { label: string; className: string; dotClass: string }
> = {
  idle: {
    label: "Idle",
    className: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
    dotClass: "bg-emerald-400",
  },
  working: {
    label: "Working",
    className: "bg-amber-500/15 text-amber-400 border-amber-500/30",
    dotClass: "bg-amber-400",
  },
  error: {
    label: "Error",
    className: "bg-red-500/15 text-red-400 border-red-500/30",
    dotClass: "bg-red-400",
  },
  dead: {
    label: "Dead",
    className: "bg-red-500/15 text-red-400 border-red-500/30",
    dotClass: "bg-red-400 animate-pulse-dot",
  },
  paused: {
    label: "Paused",
    className: "bg-slate-500/15 text-slate-400 border-slate-500/30",
    dotClass: "bg-slate-400",
  },
};

interface StatusBadgeProps {
  status: AgentStatusType;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const config = statusConfig[status] ?? statusConfig.paused;

  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5 font-medium",
        config.className,
        className
      )}
    >
      <span
        className={cn("h-1.5 w-1.5 rounded-full", config.dotClass)}
      />
      {config.label}
    </Badge>
  );
}
