import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Progress } from "~/components/ui/progress";
import { Skeleton } from "~/components/ui/skeleton";
import { fetchMilestones } from "~/lib/api";
import { cn } from "~/lib/utils";
import type { Phase } from "~/lib/types";

export function MilestoneTimeline() {
  const { data, isLoading } = useQuery({
    queryKey: ["orch-milestones"],
    queryFn: fetchMilestones,
    refetchInterval: 60_000,
  });

  if (isLoading && !data) {
    return <Skeleton className="h-[300px]" />;
  }

  if (!data || data.length === 0) {
    return (
      <Card className="border-border/50">
        <CardContent className="flex items-center justify-center py-12">
          <p className="text-sm text-muted-foreground">No milestones available</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-border/50 animate-fade-in">
      <CardHeader className="pb-3">
        <CardTitle className="text-base font-medium">Milestones</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-6">
          {data.map((phase) => (
            <PhaseSection key={phase.number} phase={phase} />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function PhaseSection({ phase }: { phase: Phase }) {
  const done = phase.milestones.filter((m) => m.done).length;
  const total = phase.milestones.length;
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;

  return (
    <div className={cn(phase.is_future && "opacity-40")}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-medium">
          Phase {phase.number}: {phase.title}
        </h3>
        <span className="text-xs text-muted-foreground">
          {done}/{total} complete
        </span>
      </div>
      <Progress value={percent} className="h-1.5 mb-3" />
      <div className="space-y-1">
        {phase.milestones.map((ms, i) => (
          <div key={i} className="flex items-start gap-2 text-xs py-0.5">
            {ms.done ? (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-3.5 w-3.5 text-emerald-400 shrink-0 mt-0.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M22 11.08V12a10 10 0 11-5.93-9.14" />
                <path d="M22 4L12 14.01l-3-3" />
              </svg>
            ) : (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-3.5 w-3.5 text-muted-foreground/40 shrink-0 mt-0.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <circle cx="12" cy="12" r="10" />
              </svg>
            )}
            <span className={cn(
              ms.done ? "text-foreground/80" : "text-muted-foreground"
            )}>
              {ms.text}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
