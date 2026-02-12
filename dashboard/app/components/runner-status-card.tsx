import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
  const [actionLoading, setActionLoading] = useState<"start" | "stop" | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["orch-status"],
    queryFn: fetchOrchestratorStatus,
    refetchInterval: 15_000,
  });

  const handleStart = async () => {
    setActionLoading("start");
    try {
      const res = await startOrchestrator();
      toast({ title: "Orchestrator started", description: res.message, variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["orch-status"] });
    } catch (err) {
      toast({
        title: "Failed to start orchestrator",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setActionLoading(null);
    }
  };

  const handleStop = async () => {
    setActionLoading("stop");
    try {
      const res = await stopOrchestrator();
      toast({ title: "Orchestrator stopped", description: res.message, variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["orch-status"] });
    } catch (err) {
      toast({
        title: "Failed to stop orchestrator",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setActionLoading(null);
    }
  };

  if (isLoading && !data) {
    return <Skeleton className="h-[180px]" />;
  }

  if (error && !data) {
    return (
      <Card className="border-destructive/30">
        <CardContent className="flex items-center justify-center py-8">
          <p className="text-sm text-destructive">Failed to load orchestrator status</p>
        </CardContent>
      </Card>
    );
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
            <span className={cn(
              "text-xs font-medium px-2 py-0.5 rounded-full",
              alive ? "bg-emerald-400/10 text-emerald-400" : "bg-destructive/10 text-destructive"
            )}>
              {alive ? "Running" : "Stopped"}
            </span>
            {alive ? (
              <Button
                variant="destructive"
                size="sm"
                disabled={actionLoading !== null}
                onClick={handleStop}
              >
                {actionLoading === "stop" ? (
                  <>
                    <svg className="animate-spin -ml-1 mr-1.5 h-3.5 w-3.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    Stopping...
                  </>
                ) : (
                  <>
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="mr-1.5 h-3.5 w-3.5"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <rect x="6" y="6" width="12" height="12" />
                    </svg>
                    Stop
                  </>
                )}
              </Button>
            ) : (
              <Button
                variant="success"
                size="sm"
                disabled={actionLoading !== null}
                onClick={handleStart}
              >
                {actionLoading === "start" ? (
                  <>
                    <svg className="animate-spin -ml-1 mr-1.5 h-3.5 w-3.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    Starting...
                  </>
                ) : (
                  <>
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="mr-1.5 h-3.5 w-3.5"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <polygon points="5 3 19 12 5 21 5 3" />
                    </svg>
                    Start
                  </>
                )}
              </Button>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-3 gap-4 text-sm">
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
