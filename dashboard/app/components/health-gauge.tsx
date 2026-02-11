import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Progress } from "~/components/ui/progress";
import { Skeleton } from "~/components/ui/skeleton";
import { fetchHealth } from "~/lib/api";
import { cn } from "~/lib/utils";

const gradeColors: Record<string, { text: string; bg: string; progress: string }> = {
  A: { text: "text-emerald-400", bg: "bg-emerald-500/10", progress: "[&>div]:bg-emerald-400" },
  B: { text: "text-blue-400", bg: "bg-blue-500/10", progress: "[&>div]:bg-blue-400" },
  C: { text: "text-amber-400", bg: "bg-amber-500/10", progress: "[&>div]:bg-amber-400" },
  D: { text: "text-orange-400", bg: "bg-orange-500/10", progress: "[&>div]:bg-orange-400" },
  F: { text: "text-red-400", bg: "bg-red-500/10", progress: "[&>div]:bg-red-400" },
};

function gradeToPercent(grade: string): number {
  switch (grade) {
    case "A": return 95;
    case "B": return 80;
    case "C": return 65;
    case "D": return 45;
    case "F": return 20;
    default: return 0;
  }
}

function getGradeStyle(grade: string) {
  return gradeColors[grade] ?? gradeColors.F;
}

const severityColors: Record<string, string> = {
  critical: "text-red-400",
  high: "text-orange-400",
  medium: "text-amber-400",
  low: "text-blue-400",
};

export function HealthGauge() {
  const { data, isLoading } = useQuery({
    queryKey: ["orch-health"],
    queryFn: fetchHealth,
    refetchInterval: 30_000,
  });

  if (isLoading && !data) {
    return <Skeleton className="h-[400px]" />;
  }

  if (!data) {
    return (
      <Card className="border-border/50">
        <CardContent className="flex items-center justify-center py-12">
          <p className="text-sm text-muted-foreground">No health data available</p>
        </CardContent>
      </Card>
    );
  }

  const overallStyle = getGradeStyle(data.overall_grade);
  const dimensions = Object.entries(data.dimensions);

  return (
    <Card className="border-border/50 animate-fade-in">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base font-medium">Health Report</CardTitle>
          <div className="flex items-center gap-2">
            <span className={cn("text-3xl font-bold", overallStyle.text)}>
              {data.overall_grade}
            </span>
            <span className="text-sm text-muted-foreground">
              {data.score}/100
            </span>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {dimensions.map(([name, dim]) => {
            const style = getGradeStyle(dim.grade);
            return (
              <div
                key={name}
                className={cn(
                  "rounded-lg border border-border/30 p-3",
                  style.bg
                )}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium capitalize">
                    {name.replace(/_/g, " ")}
                  </span>
                  <span className={cn("text-sm font-bold", style.text)}>
                    {dim.grade}
                  </span>
                </div>
                <Progress
                  value={gradeToPercent(dim.grade)}
                  className={cn("h-1.5", style.progress)}
                />
                {dim.notes && (
                  <p className="text-[10px] text-muted-foreground mt-1.5 line-clamp-1">
                    {dim.notes}
                  </p>
                )}
              </div>
            );
          })}
        </div>

        {data.problems.length > 0 && (
          <div className="mt-4 pt-4 border-t border-border/30">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/60 mb-2">
              Problems
            </p>
            <div className="space-y-1.5">
              {data.problems.map((p, i) => (
                <div key={i} className="flex items-start gap-2 text-xs">
                  <span className={cn("font-medium shrink-0 uppercase", severityColors[p.severity] ?? "text-muted-foreground")}>
                    {p.severity}
                  </span>
                  <span className="text-foreground/80">{p.description}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
