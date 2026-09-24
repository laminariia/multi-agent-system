import { useState } from "react";
import { useParams, Link } from "@remix-run/react";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Separator } from "~/components/ui/separator";
import { Skeleton } from "~/components/ui/skeleton";
import { Textarea } from "~/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { PipelineStatusTracker } from "~/components/pipeline-status-tracker";
import { ExecutionProgress } from "~/components/execution-progress";
import { fetchJob, disqualifyJob, runPipeline, fetchPipelineProgress } from "~/lib/api";
import { relativeTime } from "~/lib/utils";
import { toast } from "~/hooks/use-toast";
import { useWsSubscription } from "~/hooks/use-ws-subscription";

const PIPELINE_ELIGIBLE_STATUSES = new Set(["qualified", "bid_sent", "won"]);

const PIPELINE_STAGES = [
  { key: "scout", label: "Scout" },
  { key: "bid", label: "Bid" },
  { key: "hitl_bid", label: "HITL" },
  { key: "planner", label: "Plan" },
  { key: "dev", label: "Dev" },
  { key: "content", label: "Content" },
  { key: "design", label: "Design" },
  { key: "critic", label: "Critic" },
  { key: "hitl_review", label: "HITL" },
  { key: "packager", label: "Pack" },
] as const;

function PipelineProgressBar({ currentStage }: { currentStage?: string }) {
  const activeIdx = currentStage
    ? PIPELINE_STAGES.findIndex((s) => s.key === currentStage)
    : -1;

  return (
    <div className="flex items-center w-full">
      {PIPELINE_STAGES.map((stage, idx) => {
        const isDone = idx < activeIdx;
        const isActive = idx === activeIdx;
        return (
          <div key={stage.key} className="flex items-center flex-1 min-w-0">
            <div className="flex flex-col items-center gap-1 flex-shrink-0">
              <div
                className={`h-7 w-7 rounded-full flex items-center justify-center text-[10px] font-bold border-2 ${
                  isDone
                    ? "bg-green-500 border-green-500 text-white"
                    : isActive
                    ? "bg-orange-500 border-orange-500 text-white"
                    : "bg-zinc-800 border-zinc-700 text-zinc-500"
                }`}
              >
                {isDone ? (
                  <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                ) : (
                  <span>{idx + 1}</span>
                )}
              </div>
              <span
                className={`text-[9px] font-medium ${
                  isDone ? "text-green-400" : isActive ? "text-orange-400" : "text-zinc-600"
                }`}
              >
                {stage.label}
              </span>
            </div>
            {idx < PIPELINE_STAGES.length - 1 && (
              <div
                className={`h-0.5 flex-1 mx-1 mb-4 ${
                  idx < activeIdx ? "bg-green-500" : "bg-zinc-700"
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [showDisqualify, setShowDisqualify] = useState(false);
  const [disqualifyReason, setDisqualifyReason] = useState("");
  const [disqualifying, setDisqualifying] = useState(false);
  const [runningPipeline, setRunningPipeline] = useState(false);
  const [expandedBids, setExpandedBids] = useState<Set<string>>(new Set());
  const [activeTab, setActiveTab] = useState("details");

  useWsSubscription("subscribe:project", id);

  const { data: job, isLoading, error } = useQuery({
    queryKey: ["job", id],
    queryFn: () => fetchJob(id!),
    enabled: !!id,
  });

  const { data: pipelineProgress } = useQuery({
    queryKey: ["pipeline-progress", id],
    queryFn: () => fetchPipelineProgress(id!),
    enabled: !!id && job?.status === "in_progress",
    refetchInterval: 5000,
  });

  const handleDisqualify = async () => {
    if (!id || !disqualifyReason.trim()) return;
    setDisqualifying(true);
    try {
      await disqualifyJob(id, disqualifyReason);
      toast({ title: "Job disqualified", variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["job", id] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      setShowDisqualify(false);
    } catch (err) {
      toast({
        title: "Failed to disqualify",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setDisqualifying(false);
    }
  };

  const handleRunPipeline = async () => {
    if (!id) return;
    setRunningPipeline(true);
    try {
      const res = await runPipeline(id);
      toast({
        title: "Pipeline started",
        description: `Full pipeline running for this job. Thread: ${res.thread_id}`,
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["job", id] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    } catch (err) {
      toast({
        title: "Failed to start pipeline",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setRunningPipeline(false);
    }
  };

  const toggleBidExpanded = (bidId: string) => {
    setExpandedBids((prev) => {
      const next = new Set(prev);
      if (next.has(bidId)) {
        next.delete(bidId);
      } else {
        next.add(bidId);
      }
      return next;
    });
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-5 w-24" />
        <div className="flex items-start justify-between gap-4">
          <Skeleton className="h-8 w-72" />
          <Skeleton className="h-9 w-28" />
        </div>
        <Skeleton className="h-20" />
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2 space-y-4">
            <Skeleton className="h-[200px]" />
            <Skeleton className="h-[120px]" />
          </div>
          <div className="space-y-4">
            <Skeleton className="h-[180px]" />
            <Skeleton className="h-[120px]" />
          </div>
        </div>
      </div>
    );
  }

  if (error || !job) {
    return (
      <div className="space-y-4">
        <Link to="/jobs" className="text-sm text-orange-400 hover:underline">
          Back to Jobs
        </Link>
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error instanceof Error ? error.message : "Job not found"}
        </div>
      </div>
    );
  }

  const budgetText =
    job.budget_min != null && job.budget_max != null
      ? `$${job.budget_min} – $${job.budget_max}`
      : job.budget_min != null
      ? `$${job.budget_min}+`
      : job.budget_max != null
      ? `Up to $${job.budget_max}`
      : "Not specified";

  const canRunPipeline = PIPELINE_ELIGIBLE_STATUSES.has(job.status);

  const currentStage =
    (pipelineProgress as { current_agent?: string } | undefined)?.current_agent ??
    (job.status === "in_progress" ? "dev" : undefined);

  const statusColors: Record<string, string> = {
    new: "bg-blue-500/20 text-blue-400",
    qualified: "bg-green-500/20 text-green-400",
    bid_sent: "bg-amber-500/20 text-amber-400",
    won: "bg-emerald-500/20 text-emerald-400",
    in_progress: "bg-orange-500/20 text-orange-400",
    completed: "bg-green-500/20 text-green-400",
    on_hold: "bg-yellow-500/20 text-yellow-400",
    disqualified: "bg-red-500/20 text-red-400",
  };

  return (
    <div className="space-y-5">
      <Link
        to="/jobs"
        className="text-sm text-orange-400 hover:underline inline-flex items-center gap-1"
      >
        <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="m15 18-6-6 6-6" />
        </svg>
        Back to Jobs
      </Link>

      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold tracking-tight text-white leading-tight">
            {job.title}
          </h1>
          <div className="flex items-center gap-3 mt-2 text-sm flex-wrap">
            <Badge variant="outline" className="border-zinc-700 text-zinc-300">
              {job.platform}
            </Badge>
            <span className="text-orange-400 font-medium">{budgetText}</span>
            {job.currency && job.currency !== "USD" && (
              <span className="text-zinc-500">{job.currency}</span>
            )}
            <span className="text-zinc-500">
              Discovered {relativeTime(job.discovered_at)}
            </span>
            <Badge
              className={`text-[10px] capitalize ${statusColors[job.status] ?? "bg-zinc-700 text-zinc-300"}`}
            >
              {job.status.replace(/_/g, " ")}
            </Badge>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {canRunPipeline && (
            <Button
              size="sm"
              disabled={runningPipeline}
              onClick={handleRunPipeline}
              className="bg-orange-500 hover:bg-orange-600 text-white"
            >
              {runningPipeline ? (
                <>
                  <svg className="animate-spin -ml-1 mr-2 h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  Starting...
                </>
              ) : (
                <>
                  <svg className="mr-2 h-4 w-4" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polygon points="5 3 19 12 5 21 5 3" />
                  </svg>
                  Run Pipeline
                </>
              )}
            </Button>
          )}
          {job.url && (
            <Button variant="outline" size="sm" className="border-zinc-700" asChild>
              <a href={job.url} target="_blank" rel="noopener noreferrer">
                View on Platform
              </a>
            </Button>
          )}
          {job.status !== "disqualified" &&
            job.status !== "in_progress" &&
            job.status !== "completed" && (
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setShowDisqualify(!showDisqualify)}
              >
                Disqualify
              </Button>
            )}
        </div>
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
        <p className="text-[10px] text-zinc-500 mb-3 font-medium uppercase tracking-wider">
          Pipeline Progress
        </p>
        <PipelineProgressBar currentStage={currentStage} />
      </div>

      {job.status === "in_progress" && pipelineProgress && (pipelineProgress as { status?: string }).status !== "idle" ? (
        <ExecutionProgress progress={pipelineProgress} />
      ) : job.status === "in_progress" ? (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-4 flex items-center gap-3">
          <div className="h-3 w-3 rounded-full bg-emerald-500 animate-pulse flex-shrink-0" />
          <div>
            <p className="text-sm font-medium text-emerald-400">Pipeline A is running</p>
            <p className="text-xs text-zinc-400 mt-0.5">Waiting for progress data...</p>
          </div>
        </div>
      ) : null}

      {showDisqualify && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 space-y-3">
          <p className="text-sm font-medium text-destructive">Disqualify Job</p>
          <Textarea
            placeholder="Reason for disqualification..."
            value={disqualifyReason}
            onChange={(e) => setDisqualifyReason(e.target.value)}
            rows={2}
            className="bg-zinc-800 border-zinc-700"
          />
          <div className="flex gap-2">
            <Button
              variant="destructive"
              size="sm"
              disabled={disqualifying || !disqualifyReason.trim()}
              onClick={handleDisqualify}
            >
              {disqualifying ? "Disqualifying..." : "Confirm Disqualify"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="border-zinc-700"
              onClick={() => setShowDisqualify(false)}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="bg-zinc-900 border border-zinc-800">
          <TabsTrigger value="details">Details</TabsTrigger>
          <TabsTrigger value="bids">
            Bids
            {job.bids.length > 0 && (
              <span className="ml-1.5 text-[10px] text-zinc-500">{job.bids.length}</span>
            )}
          </TabsTrigger>
          <TabsTrigger value="pipeline">Pipeline</TabsTrigger>
          <TabsTrigger value="timeline">Timeline</TabsTrigger>
        </TabsList>
      </Tabs>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-5">

          {activeTab === "details" && (
            <>
              <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
                <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-3">
                  Description
                </p>
                {job.description ? (
                  <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
                    {job.description}
                  </p>
                ) : (
                  <p className="text-sm text-zinc-600 italic">No description provided.</p>
                )}
                {job.skills_required && job.skills_required.length > 0 && (
                  <>
                    <Separator className="my-4 bg-zinc-800" />
                    <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-2">
                      Skills Required
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {job.skills_required.map((skill) => (
                        <span
                          key={skill}
                          className="bg-zinc-800 text-zinc-300 text-xs px-2 py-0.5 rounded border border-zinc-700"
                        >
                          {skill}
                        </span>
                      ))}
                    </div>
                  </>
                )}
                {job.disqualify_reason && (
                  <>
                    <Separator className="my-4 bg-zinc-800" />
                    <div className="rounded-md bg-destructive/10 border border-destructive/30 px-3 py-2">
                      <p className="text-xs text-destructive font-medium mb-1">Disqualification Reason</p>
                      <p className="text-sm text-zinc-300">{job.disqualify_reason}</p>
                    </div>
                  </>
                )}
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {[
                  { label: "Budget", value: budgetText, sub: job.budget_type ?? "" },
                  {
                    label: "Score",
                    value: job.score != null ? `${Math.round(job.score * 100)}%` : "N/A",
                    sub: "relevance",
                  },
                  { label: "Status", value: job.status.replace(/_/g, " "), sub: "current" },
                  {
                    label: "Deadline",
                    value: job.deadline ? relativeTime(job.deadline) : "None",
                    sub: job.deadline ? new Date(job.deadline).toLocaleDateString() : "",
                  },
                ].map((stat) => (
                  <div
                    key={stat.label}
                    className="bg-zinc-900 border border-zinc-800 rounded-lg px-4 py-3"
                  >
                    <p className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1">
                      {stat.label}
                    </p>
                    <p className="text-sm font-semibold text-white capitalize truncate">
                      {stat.value}
                    </p>
                    {stat.sub && (
                      <p className="text-[10px] text-zinc-600 mt-0.5 truncate">{stat.sub}</p>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          {activeTab === "bids" && (
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
              {job.bids.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12">
                  <p className="text-zinc-500 text-sm">No bids submitted yet.</p>
                </div>
              ) : (
                <div>
                  {job.bids.map((bid, i) => {
                    const isExpanded = expandedBids.has(bid.id);
                    return (
                      <div key={bid.id} className={i > 0 ? "border-t border-zinc-800" : ""}>
                        <button
                          type="button"
                          className="w-full px-5 py-4 flex items-center justify-between hover:bg-zinc-800/50 transition-colors text-left"
                          onClick={() => toggleBidExpanded(bid.id)}
                        >
                          <div className="flex items-center gap-3">
                            <span className="font-semibold text-white text-sm">
                              ${bid.bid_amount}
                            </span>
                            <Badge
                              variant={
                                bid.status === "approved"
                                  ? "success"
                                  : bid.status === "rejected"
                                  ? "destructive"
                                  : bid.status === "submitted"
                                  ? "default"
                                  : "outline"
                              }
                              className="capitalize text-xs"
                            >
                              {bid.status}
                            </Badge>
                          </div>
                          <div className="flex items-center gap-3">
                            <span className="text-xs text-zinc-500">
                              {relativeTime(bid.created_at)}
                            </span>
                            <svg
                              xmlns="http://www.w3.org/2000/svg"
                              className={`h-4 w-4 text-zinc-500 transition-transform ${isExpanded ? "rotate-180" : ""}`}
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            >
                              <polyline points="6 9 12 15 18 9" />
                            </svg>
                          </div>
                        </button>
                        {isExpanded && (
                          <div className="border-t border-zinc-800 px-5 py-4 bg-zinc-950/50">
                            <div className="grid grid-cols-3 gap-4 text-sm">
                              <div>
                                <p className="text-zinc-500 text-xs mb-1">Amount</p>
                                <p className="font-medium text-white">${bid.bid_amount}</p>
                              </div>
                              <div>
                                <p className="text-zinc-500 text-xs mb-1">Status</p>
                                <p className="font-medium text-white capitalize">{bid.status}</p>
                              </div>
                              <div>
                                <p className="text-zinc-500 text-xs mb-1">Submitted</p>
                                <p className="font-medium text-white">
                                  {new Date(bid.created_at).toLocaleString()}
                                </p>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {activeTab === "pipeline" && (
            <div className="space-y-4">
              <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
                <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-4">
                  Pipeline Status
                </p>
                <PipelineStatusTracker status={job.status} />
              </div>
              {job.status === "in_progress" && pipelineProgress && (
                <ExecutionProgress progress={pipelineProgress} />
              )}
              {job.status !== "in_progress" && (
                <div className="bg-zinc-900 border border-zinc-800 rounded-lg px-5 py-8 flex flex-col items-center gap-2">
                  <p className="text-zinc-500 text-sm text-center">
                    {canRunPipeline
                      ? "Pipeline is ready to run for this job."
                      : `Pipeline not active (status: ${job.status}).`}
                  </p>
                  {canRunPipeline && (
                    <Button
                      size="sm"
                      onClick={handleRunPipeline}
                      disabled={runningPipeline}
                      className="mt-2 bg-orange-500 hover:bg-orange-600 text-white"
                    >
                      {runningPipeline ? "Starting..." : "Run Pipeline"}
                    </Button>
                  )}
                </div>
              )}
            </div>
          )}

          {activeTab === "timeline" && (
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
              <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-4">
                Activity Timeline
              </p>
              <div className="space-y-4">
                <TimelineEntry
                  label="Discovered"
                  time={job.discovered_at}
                  icon={<div className="h-2.5 w-2.5 rounded-full bg-blue-500" />}
                />
                {job.bids.length > 0 && (
                  <TimelineEntry
                    label={`Bid submitted — $${job.bids[0].bid_amount}`}
                    time={job.bids[0].created_at}
                    icon={<div className="h-2.5 w-2.5 rounded-full bg-amber-500" />}
                  />
                )}
                {job.status === "in_progress" && (
                  <TimelineEntry
                    label="Pipeline A running"
                    icon={<div className="h-2.5 w-2.5 rounded-full bg-emerald-500 animate-pulse" />}
                  />
                )}
                {job.status === "completed" && (
                  <TimelineEntry
                    label="Completed"
                    icon={<div className="h-2.5 w-2.5 rounded-full bg-emerald-500" />}
                  />
                )}
                {job.status === "disqualified" && (
                  <TimelineEntry
                    label={`Disqualified: ${job.disqualify_reason ?? ""}`}
                    icon={<div className="h-2.5 w-2.5 rounded-full bg-rose-500" />}
                  />
                )}
              </div>
            </div>
          )}
        </div>

        <div className="space-y-4">
          {job.client_info && Object.keys(job.client_info).length > 0 && (
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
              <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-3">
                Client Info
              </p>
              <div className="space-y-2.5">
                {Object.entries(job.client_info).map(([key, value]) => (
                  <div key={key} className="flex items-start justify-between gap-2 text-sm">
                    <span className="text-zinc-500 capitalize shrink-0">
                      {key.replace(/_/g, " ")}
                    </span>
                    <span className="text-white text-right truncate max-w-[60%]">
                      {String(value)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-3">
              Quick Stats
            </p>
            <div className="space-y-2.5 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Platform</span>
                <Badge variant="outline" className="border-zinc-700 text-zinc-300 text-xs">
                  {job.platform}
                </Badge>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Budget Type</span>
                <span className="text-white">{job.budget_type ?? "N/A"}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Score</span>
                <span>
                  {job.score != null ? (
                    <span
                      className={
                        job.score >= 0.7
                          ? "text-green-400"
                          : job.score >= 0.4
                          ? "text-amber-400"
                          : "text-red-400"
                      }
                    >
                      {Math.round(job.score * 100)}%
                    </span>
                  ) : (
                    <span className="text-white">N/A</span>
                  )}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Bids</span>
                <span className="text-white">{job.bids.length}</span>
              </div>
              {job.deadline && (
                <div className="flex items-center justify-between">
                  <span className="text-zinc-500">Deadline</span>
                  <span className="text-white">{relativeTime(job.deadline)}</span>
                </div>
              )}
            </div>
          </div>

          {(canRunPipeline ||
            (job.status !== "disqualified" &&
              job.status !== "in_progress" &&
              job.status !== "completed") ||
            job.url) && (
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
              <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-3">
                Actions
              </p>
              <div className="space-y-2">
                {canRunPipeline && (
                  <Button
                    className="w-full bg-orange-500 hover:bg-orange-600 text-white"
                    size="sm"
                    disabled={runningPipeline}
                    onClick={handleRunPipeline}
                  >
                    {runningPipeline ? "Starting..." : "Run Pipeline"}
                  </Button>
                )}
                {job.url && (
                  <Button
                    variant="outline"
                    className="w-full border-zinc-700"
                    size="sm"
                    asChild
                  >
                    <a href={job.url} target="_blank" rel="noopener noreferrer">
                      View on Platform
                    </a>
                  </Button>
                )}
                {job.status !== "disqualified" &&
                  job.status !== "in_progress" &&
                  job.status !== "completed" && (
                    <Button
                      variant="ghost"
                      className="w-full text-destructive hover:text-destructive hover:bg-destructive/10"
                      size="sm"
                      onClick={() => setShowDisqualify(true)}
                    >
                      Disqualify
                    </Button>
                  )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function TimelineEntry({
  label,
  time,
  icon,
}: {
  label: string;
  time?: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="mt-1.5 flex-shrink-0">{icon}</div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-zinc-300">{label}</p>
        {time && (
          <p className="text-xs text-zinc-500 mt-0.5">
            {new Date(time).toLocaleString()}
          </p>
        )}
      </div>
    </div>
  );
}
