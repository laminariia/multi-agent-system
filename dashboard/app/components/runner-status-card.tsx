import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Skeleton } from "~/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogTrigger,
} from "~/components/ui/dialog";
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
  const [startOpen, setStartOpen] = useState(false);
  const [totalHours, setTotalHours] = useState("8");
  const [sessionMinutes, setSessionMinutes] = useState("30");

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
      setStartOpen(false);
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
              <Dialog open={startOpen} onOpenChange={setStartOpen}>
                <DialogTrigger asChild>
                  <Button size="sm">Start</Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Start Orchestrator</DialogTitle>
                    <DialogDescription>
                      Configure session parameters before starting the runner.
                    </DialogDescription>
                  </DialogHeader>
                  <div className="grid gap-4 py-4">
                    <div className="grid gap-2">
                      <Label htmlFor="total-hours">Total Hours</Label>
                      <Input
                        id="total-hours"
                        type="number"
                        min="1"
                        max="168"
                        value={totalHours}
                        onChange={(e) => setTotalHours(e.target.value)}
                      />
                    </div>
                    <div className="grid gap-2">
                      <Label htmlFor="session-minutes">Session Minutes</Label>
                      <Input
                        id="session-minutes"
                        type="number"
                        min="5"
                        max="120"
                        value={sessionMinutes}
                        onChange={(e) => setSessionMinutes(e.target.value)}
                      />
                    </div>
                  </div>
                  <DialogFooter>
                    <Button
                      disabled={startMutation.isPending}
                      onClick={() => {
                        startMutation.mutate({
                          total_hours: Number(totalHours) || 8,
                          session_minutes: Number(sessionMinutes) || 30,
                        });
                      }}
                    >
                      {startMutation.isPending ? "Starting..." : "Start Runner"}
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
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
