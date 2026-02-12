import { cn } from "~/lib/utils";

interface PipelineStep {
  key: string;
  label: string;
}

const PIPELINE_A_STEPS: PipelineStep[] = [
  { key: "discovered", label: "Discovered" },
  { key: "qualified", label: "Qualified" },
  { key: "bid_sent", label: "Bid Sent" },
  { key: "in_progress", label: "In Progress" },
  { key: "completed", label: "Completed" },
];

interface PipelineStatusTrackerProps {
  status: string;
  className?: string;
}

function getStepIndex(status: string): number {
  const idx = PIPELINE_A_STEPS.findIndex((s) => s.key === status);
  // For statuses not in the pipeline (e.g., "disqualified"), return -1
  return idx;
}

export function PipelineStatusTracker({
  status,
  className,
}: PipelineStatusTrackerProps) {
  const activeIndex = getStepIndex(status);
  const isDisqualified = status === "disqualified";

  if (isDisqualified) {
    return (
      <div className={cn("flex items-center gap-2 py-3", className)}>
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-destructive/20 text-destructive">
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
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </div>
        <span className="text-sm font-medium text-destructive">
          Disqualified
        </span>
      </div>
    );
  }

  return (
    <div className={cn("w-full", className)}>
      <div className="flex items-center">
        {PIPELINE_A_STEPS.map((step, i) => {
          const isCompleted = activeIndex > i;
          const isActive = activeIndex === i;
          const isPending = activeIndex < i;

          return (
            <div key={step.key} className="flex flex-1 items-center">
              {/* Step circle */}
              <div className="flex flex-col items-center gap-1.5 flex-shrink-0">
                <div
                  className={cn(
                    "flex h-7 w-7 items-center justify-center rounded-full border-2 transition-all",
                    isCompleted &&
                      "border-primary bg-primary text-primary-foreground",
                    isActive &&
                      "border-primary bg-primary/10 text-primary animate-pulse",
                    isPending &&
                      "border-muted-foreground/30 bg-muted/50 text-muted-foreground/50"
                  )}
                >
                  {isCompleted ? (
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="h-3.5 w-3.5"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="3"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  ) : (
                    <span className="text-[10px] font-semibold">{i + 1}</span>
                  )}
                </div>
                <span
                  className={cn(
                    "text-[10px] font-medium whitespace-nowrap",
                    isCompleted && "text-primary",
                    isActive && "text-primary font-semibold",
                    isPending && "text-muted-foreground/50"
                  )}
                >
                  {step.label}
                </span>
              </div>

              {/* Connector line */}
              {i < PIPELINE_A_STEPS.length - 1 && (
                <div
                  className={cn(
                    "h-0.5 flex-1 mx-1.5 -mt-5 rounded-full transition-colors",
                    activeIndex > i
                      ? "bg-primary"
                      : "bg-muted-foreground/20"
                  )}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
