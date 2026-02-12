import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Button } from "~/components/ui/button";
import { MetricCard } from "~/components/metric-card";
import { AgentCard } from "~/components/agent-card";
import { PipelineFlow } from "~/components/pipeline-flow";
import { ActivityFeed, type ActivityEvent } from "~/components/activity-feed";
import { JobsByPlatformChart, HITLByTypeChart, AgentStatusChart, HITLTrendsChart } from "~/components/charts";
import { SkeletonCard, SkeletonGrid } from "~/components/skeleton-card";
import { EmptyState } from "~/components/empty-state";
import { fetchAgentStatus, fetchHITLStats, fetchJobs, fetchJobStats, fetchHITLTrends, fetchPipelineBStats } from "~/lib/api";
import { OrchStatusWidget } from "~/components/orch-status-widget";

export default function DashboardPage() {
  const queryClient = useQueryClient();

  const { data: agentData, isLoading: agentsLoading, error: agentError } = useQuery({
    queryKey: ["agent-status"],
    queryFn: fetchAgentStatus,
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  const { data: hitlStats } = useQuery({
    queryKey: ["hitl-stats"],
    queryFn: fetchHITLStats,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const { data: jobsData } = useQuery({
    queryKey: ["jobs", "active"],
    queryFn: () => fetchJobs({ limit: 50 }),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const { data: jobStats } = useQuery({
    queryKey: ["job-stats"],
    queryFn: fetchJobStats,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const { data: hitlTrends } = useQuery({
    queryKey: ["hitl-trends"],
    queryFn: () => fetchHITLTrends(7),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });

  const { data: pipelineBStats } = useQuery({
    queryKey: ["pipeline-b-stats"],
    queryFn: fetchPipelineBStats,
    staleTime: 60_000,
    refetchInterval: 120_000,
  });

  const agents = agentData?.agents ?? [];
  const workingAgents = agents.filter((a) => a.status === "working" || a.status === "idle").length;
  const activeJobs = useMemo(() => {
    const byStatus = jobStats?.by_status ?? {};
    return (byStatus["in_progress"] ?? 0) + (byStatus["qualified"] ?? 0) + (byStatus["bid_sent"] ?? 0);
  }, [jobStats]);
  const pendingHitl = hitlStats?.today?.pending ?? 0;

  // Chart data: jobs by platform (from stats endpoint)
  const jobsByPlatform = useMemo(() => {
    return (jobStats?.by_platform ?? []).map((item) => ({
      platform: item.platform.replace("_", "."),
      count: item.count,
    }));
  }, [jobStats]);

  // Chart data: HITL by type
  const hitlByType = useMemo(() => {
    if (!hitlStats?.by_type) return [];
    return Object.entries(hitlStats.by_type).map(([type, data]) => ({
      type: type.replace("_", " "),
      pending: data.pending,
      resolved: data.resolved,
    }));
  }, [hitlStats]);

  // Chart data: agent status distribution
  const agentStatusDist = useMemo(() => {
    const counts: Record<string, number> = {};
    agents.forEach((a) => {
      counts[a.status] = (counts[a.status] ?? 0) + 1;
    });
    return Object.entries(counts).map(([status, count]) => ({ status, count }));
  }, [agents]);

  // Mock activity feed from agent data
  const recentActivity: ActivityEvent[] = useMemo(() => {
    const events: ActivityEvent[] = [];
    agents.forEach((a) => {
      if (a.status === "working" && a.current_task) {
        events.push({
          id: `working-${a.name}`,
          type: "agent_started",
          message: a.current_task,
          timestamp: a.last_heartbeat ?? new Date().toISOString(),
          agent: a.display_name ?? a.name,
        });
      }
      if (a.status === "error" && a.error_message) {
        events.push({
          id: `error-${a.name}`,
          type: "agent_error",
          message: a.error_message,
          timestamp: a.last_heartbeat ?? new Date().toISOString(),
          agent: a.display_name ?? a.name,
        });
      }
    });
    return events.slice(0, 20);
  }, [agents]);

  const hasError = !!agentError;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>

      {/* Error banner */}
      {hasError && (
        <div className="flex items-center justify-between rounded-lg border border-destructive/50 bg-destructive/10 p-4">
          <p className="text-sm text-destructive">
            Failed to load dashboard data. The API may be unavailable.
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => queryClient.invalidateQueries()}
          >
            Retry
          </Button>
        </div>
      )}

      {/* Metric cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {agentsLoading && !agentData ? (
          <>
            {Array.from({ length: 5 }).map((_, i) => (
              <SkeletonCard key={i} variant="metric" />
            ))}
          </>
        ) : (
          <>
            <MetricCard
              index={0}
              icon={
                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2 2v2m4 6h.01M5 20h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
              }
              label="Active Jobs"
              value={activeJobs}
            />
            <MetricCard
              index={1}
              icon={
                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
              }
              label="Pending HITL"
              value={pendingHitl}
            />
            <MetricCard
              index={2}
              icon={
                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
                </svg>
              }
              label="Agents Online"
              value={`${workingAgents}/${agents.length}`}
            />
            <MetricCard
              index={3}
              icon={
                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
                  <polyline points="17 6 23 6 23 12" />
                </svg>
              }
              label="Resolved Today"
              value={hitlStats?.today?.resolved ?? 0}
            />
            <OrchStatusWidget />
          </>
        )}
      </div>

      {/* Pipeline Flow */}
      <Card className="border-border/50">
        <CardHeader className="pb-3">
          <CardTitle className="text-base font-medium">Pipeline Status</CardTitle>
        </CardHeader>
        <CardContent>
          {agentsLoading && !agentData ? (
            <div className="animate-shimmer rounded-md bg-gradient-to-r from-muted via-muted/50 to-muted bg-[length:200%_100%] h-[80px]" />
          ) : (
            <PipelineFlow agents={agents} />
          )}
        </CardContent>
      </Card>

      {/* Charts row */}
      {(jobsByPlatform.length > 0 || hitlByType.length > 0 || agentStatusDist.length > 0) && (
        <div className="grid gap-4 md:grid-cols-3">
          <JobsByPlatformChart data={jobsByPlatform} />
          <HITLByTypeChart data={hitlByType} />
          <AgentStatusChart data={agentStatusDist} />
        </div>
      )}

      {/* HITL Trends chart */}
      {hitlTrends && hitlTrends.trends.length > 0 && (
        <HITLTrendsChart data={[...hitlTrends.trends].reverse()} />
      )}

      {/* Pipeline B Summary */}
      {pipelineBStats && pipelineBStats.total_leads > 0 && (
        <Card className="border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-medium">Pipeline B — Leads</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-lg border border-border/50 p-3">
                <p className="text-xs text-muted-foreground">Total Leads</p>
                <p className="text-2xl font-semibold">{pipelineBStats.total_leads}</p>
              </div>
              <div className="rounded-lg border border-border/50 p-3">
                <p className="text-xs text-muted-foreground">Enriched</p>
                <p className="text-2xl font-semibold">{pipelineBStats.by_status?.["enriched"] ?? 0}</p>
              </div>
              <div className="rounded-lg border border-border/50 p-3">
                <p className="text-xs text-muted-foreground">Contacted</p>
                <p className="text-2xl font-semibold">{pipelineBStats.by_status?.["contacted"] ?? 0}</p>
              </div>
              <div className="rounded-lg border border-border/50 p-3">
                <p className="text-xs text-muted-foreground">Top Cities</p>
                <p className="text-sm font-medium truncate">
                  {pipelineBStats.top_cities?.slice(0, 3).map((c) => c.city).join(", ") || "—"}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Two-column layout: Agent Grid + Activity */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Agent Status Grid */}
        <div className="lg:col-span-2">
          <h2 className="text-base font-medium mb-4">Agent Status</h2>
          {agentsLoading && !agentData ? (
            <SkeletonGrid count={6} variant="agent" />
          ) : agents.length > 0 ? (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {agents.map((agent) => (
                <AgentCard key={agent.name} agent={agent} />
              ))}
            </div>
          ) : (
            <EmptyState
              title="No agents registered"
              description="Agents will appear once they start sending heartbeats"
              icon={
                <svg xmlns="http://www.w3.org/2000/svg" className="h-10 w-10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
                </svg>
              }
            />
          )}
        </div>

        {/* Activity Feed */}
        <div>
          <h2 className="text-base font-medium mb-4">Recent Activity</h2>
          <Card className="border-border/50">
            <CardContent className="p-2">
              <ActivityFeed events={recentActivity} />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
