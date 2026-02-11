import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { fetchOrchestratorStatus, startOrchestrator, stopOrchestrator } from "~/lib/api";
import { toast } from "~/hooks/use-toast";
import { cn } from "~/lib/utils";

function formatUptime(seconds: number | null): string {
  if (seconds == null) return "--";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function RunnerStatusCard() {
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["orch-status"],
    queryFn: fetchOrchestratorStatus,
    refetchInterval: 15_000,
  });

  const startMutation = useMutation({
    mutationFn: startOrchestrator,
    onSuccess: (res) => {
      toast({ title: "Orchestrator started", description: res.message });
      queryClient.invalidateQueries({ queryKey: ["orch-status"] });
    },
    onError: (err) => {
      toast({
        title: "Failed to start",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    },
  });

  const stopMutation = useMutation({
    mutationFn: stopOrchestrator,
    onSuccess: (res) => {
      toast({ title: "Orchestrator stopped", description: res.message });
      queryClient.invalidateQueries({ queryKey: ["orch-status"] });
    },
    onError: (err) => {
      toast({
        title: "Failed to stop",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    },
  });

  if (isLoading && !data) {
    return <Skeleton className="h-[180px]" />;
  }

  const alive = data?.alive ?? false;

  return (
    <Card className="border-border/50 animate-fade-in">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base font-medium flex items-center gap-2.5">
            <span
              className={cn(
                "h-2.5 w-2.5 rounded-full",
                alive ? "bg-emerald-400" : "bg-destructive"
              )}
            />
            Orchestrator Runner
          </CardTitle>
          <div className="flex items-center gap-2">
            {alive ? (
              <Button
                variant="destructive"
                size="sm"
                disabled={stopMutation.isPending}
                onClick={() => stopMutation.mutate()}
              >
                {stopMutation.isPending ? "Stopping..." : "Stop"}
              </Button>
            ) : (
              <Button
                size="sm"
                disabled={startMutation.isPending}
                onClick={() => startMutation.mutate()}
              >
                {startMutation.isPending ? "Starting..." : "Start"}
              </Button>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div>
            <p className="text-muted-foreground text-xs">Status</p>
            <p className={cn("font-medium", alive ? "text-emerald-400" : "text-destructive")}>
              {alive ? "Running" : "Stopped"}
            </p>
          </div>
          <div>
            <p className="text-muted-foreground text-xs">PID</p>
            <p className="font-medium">{data?.pid ?? "--"}</p>
          </div>
          <div>
            <p className="text-muted-foreground text-xs">Uptime</p>
            <p className="font-medium">{formatUptime(data?.uptime_seconds ?? null)}</p>
          </div>
          <div>
            <p className="text-muted-foreground text-xs">Mode</p>
            <p className="font-medium">{data?.mode ?? "--"}</p>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-4 mt-4 pt-4 border-t border-border/30 text-sm">
          <div>
            <p className="text-muted-foreground text-xs">Pending Goals</p>
            <p className="text-lg font-semibold">{data?.goals_pending ?? 0}</p>
          </div>
          <div>
            <p className="text-muted-foreground text-xs">Completed</p>
            <p className="text-lg font-semibold text-emerald-400">{data?.goals_completed ?? 0}</p>
          </div>
          <div>
            <p className="text-muted-foreground text-xs">Failed</p>
            <p className="text-lg font-semibold text-destructive">{data?.goals_failed ?? 0}</p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
