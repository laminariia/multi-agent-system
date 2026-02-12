import { useNavigate } from "@remix-run/react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent } from "~/components/ui/card";
import { Skeleton } from "~/components/ui/skeleton";
import { fetchOrchestratorStatus } from "~/lib/api";
import { cn } from "~/lib/utils";

export function OrchStatusWidget() {
  const navigate = useNavigate();

  const { data, isLoading, error } = useQuery({
    queryKey: ["orch-status"],
    queryFn: fetchOrchestratorStatus,
    refetchInterval: 30_000,
  });

  if (isLoading && !data) {
    return <Skeleton className="h-[120px]" />;
  }

  if (error && !data) {
    return (
      <Card className="border-destructive/30">
        <CardContent className="flex items-center justify-center p-5">
          <p className="text-xs text-destructive">Failed to load orchestrator status</p>
        </CardContent>
      </Card>
    );
  }

  const alive = data?.alive ?? false;
  const grade = data?.health_grade ?? "--";
  const pending = data?.goals_pending ?? 0;

  return (
    <Card
      className="border-border/50 animate-fade-in cursor-pointer hover:border-primary/30 hover:shadow-lg hover:shadow-primary/5 transition-all"
      onClick={() => navigate("/orchestrator")}
    >
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-5 w-5"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "h-2.5 w-2.5 rounded-full",
                alive ? "bg-emerald-400" : "bg-destructive"
              )}
            />
            <span className="text-xs text-muted-foreground">
              {alive ? "Running" : "Stopped"}
            </span>
          </div>
        </div>
        <div className="mt-3 flex items-end justify-between">
          <div>
            <p className="text-2xl font-bold tracking-tight">{grade}</p>
            <p className="text-xs text-muted-foreground mt-0.5">Health Grade</p>
          </div>
          {pending > 0 && (
            <span className="text-xs text-muted-foreground">
              {pending} goals pending
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
