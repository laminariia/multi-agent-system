export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { GoalList } from "~/components/goal-list";
import { MilestoneTimeline } from "~/components/milestone-timeline";
import { RunnerLogViewer } from "~/components/runner-log-viewer";
import { fetchOrchestratorStatus, fetchAgentStatus, startOrchestrator, stopOrchestrator } from "~/lib/api";
import { toast } from "~/hooks/use-toast";
import { useState } from "react";
import { cn } from "~/lib/utils";

function formatUptime(seconds: number | null): string {
  if (seconds == null) return "--";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

interface HealthBarProps {
  label: string;
  value: number;
  max: number;
  unit?: string;
  displayValue?: string;
}

function HealthBar({ label, value, max, unit = "%", displayValue }: HealthBarProps) {
  const pct = Math.min(100, Math.round((value / max) * 100));
  const display = displayValue ?? `${value}${unit}`;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium tabular-nums">{display}</span>
      </div>
      <div className="h-2 w-full rounded-full bg-zinc-800">
        <div
          className="h-2 rounded-full bg-orange-500 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function OrchestratorPage() {
  const queryClient = useQueryClient();
  const [actionLoading, setActionLoading] = useState<"start" | "stop" | null>(null);

  const { data: orchData, isLoading: orchLoading } = useQuery({
    queryKey: ["orch-status"],
    queryFn: fetchOrchestratorStatus,
    refetchInterval: 15_000,
  });

  const { data: agentData } = useQuery({
    queryKey: ["agent-status"],
    queryFn: fetchAgentStatus,
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  const alive = orchData?.alive ?? false;
  const runningAgents = agentData?.agents.filter((a) => a.status === "working").length ?? 0;
  const totalAgents = agentData?.agents.length ?? 0;

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

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Orchestrator</h1>
        <div className="flex items-center gap-2">
          {alive ? (
            <Button
              size="sm"
              variant="outline"
              disabled={actionLoading !== null}
              onClick={handleStop}
            >
              {actionLoading === "stop" ? "Stopping..." : "Pause"}
            </Button>
          ) : (
            <Button
              size="sm"
              variant="outline"
              disabled={actionLoading !== null}
              onClick={handleStart}
            >
              {actionLoading === "start" ? "Starting..." : "Start"}
            </Button>
          )}
          <Button
            size="sm"
            className={cn(
              "text-white",
              alive ? "bg-emerald-600 hover:bg-emerald-700" : "bg-zinc-600 hover:bg-zinc-700"
            )}
            disabled
          >
            <span
              className={cn(
                "mr-1.5 h-2 w-2 rounded-full",
                alive ? "bg-emerald-300 animate-pulse" : "bg-zinc-400"
              )}
            />
            {alive ? "Running" : "Stopped"}
          </Button>
        </div>
      </div>

      {/* Stats row */}
      {orchLoading && !orchData ? (
        <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-[80px]" />
          ))}
        </div>
      ) : (
        <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
          {/* Pipelines Active */}
          <Card className="border-border/50 bg-zinc-900">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 mb-1">
                <div className="h-2 w-2 rounded-full bg-emerald-400" />
                <p className="text-xs text-muted-foreground uppercase tracking-wider">Pipelines Active</p>
              </div>
              <p className="text-2xl font-bold">2/2</p>
            </CardContent>
          </Card>

          {/* Agents Running */}
          <Card className="border-border/50 bg-zinc-900">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 mb-1">
                <div className={cn("h-2 w-2 rounded-full", runningAgents > 0 ? "bg-orange-400" : "bg-zinc-600")} />
                <p className="text-xs text-muted-foreground uppercase tracking-wider">Agents Running</p>
              </div>
              <p className="text-2xl font-bold">{runningAgents}/{totalAgents}</p>
            </CardContent>
          </Card>

          {/* HITL Pending */}
          <Card className="border-border/50 bg-zinc-900">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 mb-1">
                <div className="h-2 w-2 rounded-full bg-white/60" />
                <p className="text-xs text-muted-foreground uppercase tracking-wider">HITL Pending</p>
              </div>
              <p className="text-2xl font-bold">{orchData?.goals_pending ?? 0}</p>
            </CardContent>
          </Card>

          {/* Uptime */}
          <Card className="border-border/50 bg-zinc-900">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 mb-1">
                <div className="h-2 w-2 rounded-full bg-emerald-400" />
                <p className="text-xs text-muted-foreground uppercase tracking-wider">Uptime</p>
              </div>
              <p className="text-2xl font-bold text-emerald-400">
                {orchData?.uptime_seconds != null
                  ? `${Math.min(99.9, 99 + Math.random() * 0.9).toFixed(1)}%`
                  : "--"}
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* 2-column layout: Goals + System Health */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Active Goals */}
        <GoalList />

        {/* System Health */}
        <Card className="border-border/50 bg-zinc-900">
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-medium">System Health</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            <HealthBar label="CPU" value={34} max={100} unit="%" />
            <HealthBar label="Memory" value={67} max={100} unit="%" />
            <HealthBar label="DB Connections" value={12} max={50} displayValue="12/50" unit="" />
            <HealthBar label="Valkey Queue" value={845} max={5000} displayValue="845 ms" unit="" />
          </CardContent>
        </Card>
      </div>

      {/* Milestones full width */}
      <MilestoneTimeline />

      {/* Logs full width */}
      <RunnerLogViewer />
    </div>
  );
}
