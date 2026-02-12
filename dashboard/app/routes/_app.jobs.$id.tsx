import { useState } from "react";
import { useParams, Link } from "@remix-run/react";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Separator } from "~/components/ui/separator";
import { Skeleton } from "~/components/ui/skeleton";
import { Textarea } from "~/components/ui/textarea";
import { PipelineStatusTracker } from "~/components/pipeline-status-tracker";
import { fetchJob, disqualifyJob, runPipeline } from "~/lib/api";
import { relativeTime } from "~/lib/utils";
import { toast } from "~/hooks/use-toast";
import { useWsSubscription } from "~/hooks/use-ws-subscription";

const PIPELINE_ELIGIBLE_STATUSES = new Set(["qualified", "bid_sent", "won"]);

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [showDisqualify, setShowDisqualify] = useState(false);
  const [disqualifyReason, setDisqualifyReason] = useState("");
  const [disqualifying, setDisqualifying] = useState(false);
  const [runningPipeline, setRunningPipeline] = useState(false);
  const [expandedBids, setExpandedBids] = useState<Set<string>>(new Set());

  // Subscribe to project-specific WebSocket channel for real-time updates
  useWsSubscription("subscribe:project", id);

  const { data: job, isLoading, error } = useQuery({
    queryKey: ["job", id],
    queryFn: () => fetchJob(id!),
    enabled: !!id,
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
        <Skeleton className="h-16" />
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
        <Link to="/jobs" className="text-sm text-primary hover:underline">
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

  return (
    <div className="space-y-6">
      {/* Back link */}
      <Link to="/jobs" className="text-sm text-primary hover:underline inline-flex items-center gap-1">
        <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="m15 18-6-6 6-6" />
        </svg>
        Back to Jobs
      </Link>

      {/* Job header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{job.title}</h1>
          <div className="flex items-center gap-3 mt-2 text-sm text-muted-foreground">
            <Badge variant="outline">{job.platform}</Badge>
            <span>{budgetText} {job.currency}</span>
            <span>Discovered {relativeTime(job.discovered_at)}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {canRunPipeline && (
            <Button
              variant="default"
              size="sm"
              disabled={runningPipeline}
              onClick={handleRunPipeline}
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
            <Button variant="outline" size="sm" asChild>
              <a href={job.url} target="_blank" rel="noopener noreferrer">
                View on Platform
              </a>
            </Button>
          )}
          {job.status !== "disqualified" && job.status !== "in_progress" && job.status !== "completed" && (
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

      {/* Pipeline Status Tracker */}
      <Card className="border-border/50">
        <CardContent className="py-4 px-6">
          <PipelineStatusTracker status={job.status} />
        </CardContent>
      </Card>

      {/* In progress banner */}
      {job.status === "in_progress" && (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-4 flex items-center gap-3">
          <div className="h-3 w-3 rounded-full bg-emerald-500 animate-pulse flex-shrink-0" />
          <div>
            <p className="text-sm font-medium text-emerald-700 dark:text-emerald-400">
              Pipeline A is running
            </p>
            <p className="text-xs text-muted-foreground mt-0.5">
              Planner, Dev, Content, Design, and Critic agents are processing this job.
              Check HITL queue for any pending approvals.
            </p>
          </div>
        </div>
      )}

      {/* Disqualify form */}
      {showDisqualify && (
        <Card className="border-destructive/30">
          <CardContent className="p-4 space-y-3">
            <Textarea
              placeholder="Reason for disqualification..."
              value={disqualifyReason}
              onChange={(e) => setDisqualifyReason(e.target.value)}
              rows={2}
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
                onClick={() => setShowDisqualify(false)}
              >
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Job details */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-base">Details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {job.description && (
            <div>
              <p className="text-sm text-muted-foreground mb-1">Description</p>
              <p className="text-sm whitespace-pre-wrap">{job.description}</p>
            </div>
          )}

          <Separator />

          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-muted-foreground">Budget Type</p>
              <p className="font-medium">{job.budget_type ?? "Not specified"}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Score</p>
              <p className="font-medium">
                {job.score != null ? `${Math.round(job.score * 100)}%` : "N/A"}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Status</p>
              <p className="font-medium capitalize">{job.status}</p>
            </div>
            {job.deadline && (
              <div>
                <p className="text-muted-foreground">Deadline</p>
                <p className="font-medium">{relativeTime(job.deadline)}</p>
              </div>
            )}
          </div>

          {job.skills_required && job.skills_required.length > 0 && (
            <>
              <Separator />
              <div>
                <p className="text-sm text-muted-foreground mb-2">Skills Required</p>
                <div className="flex flex-wrap gap-1.5">
                  {job.skills_required.map((skill) => (
                    <Badge key={skill} variant="secondary" className="text-xs">
                      {skill}
                    </Badge>
                  ))}
                </div>
              </div>
            </>
          )}

          {job.disqualify_reason && (
            <>
              <Separator />
              <div>
                <p className="text-sm text-destructive mb-1">Disqualified</p>
                <p className="text-sm">{job.disqualify_reason}</p>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Client info */}
      {job.client_info && Object.keys(job.client_info).length > 0 && (
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base">Client Info</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4 text-sm">
              {Object.entries(job.client_info).map(([key, value]) => (
                <div key={key}>
                  <p className="text-muted-foreground capitalize">{key.replace(/_/g, " ")}</p>
                  <p className="font-medium">{String(value)}</p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Bids */}
      {job.bids.length > 0 && (
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base">Bids ({job.bids.length})</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {job.bids.map((bid) => {
                const isExpanded = expandedBids.has(bid.id);
                return (
                  <div
                    key={bid.id}
                    className="rounded-md border border-border/50 overflow-hidden"
                  >
                    <button
                      type="button"
                      className="w-full p-4 flex items-center justify-between hover:bg-accent/30 transition-colors text-left"
                      onClick={() => toggleBidExpanded(bid.id)}
                    >
                      <div className="flex items-center gap-3">
                        <p className="text-sm font-semibold">${bid.bid_amount}</p>
                        <Badge
                          variant={
                            bid.status === "approved" ? "success" :
                            bid.status === "rejected" ? "destructive" :
                            bid.status === "submitted" ? "default" :
                            "outline"
                          }
                          className="capitalize text-xs"
                        >
                          {bid.status}
                        </Badge>
                      </div>
                      <div className="flex items-center gap-2">
                        <p className="text-xs text-muted-foreground">
                          {relativeTime(bid.created_at)}
                        </p>
                        <svg
                          xmlns="http://www.w3.org/2000/svg"
                          className={`h-4 w-4 text-muted-foreground transition-transform ${isExpanded ? "rotate-180" : ""}`}
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
                      <div className="border-t border-border/50 p-4 bg-muted/20 space-y-3 animate-in slide-in-from-top-1">
                        <div className="grid grid-cols-2 gap-3 text-sm">
                          <div>
                            <p className="text-muted-foreground text-xs">Amount</p>
                            <p className="font-medium">${bid.bid_amount}</p>
                          </div>
                          <div>
                            <p className="text-muted-foreground text-xs">Status</p>
                            <p className="font-medium capitalize">{bid.status}</p>
                          </div>
                          <div>
                            <p className="text-muted-foreground text-xs">Submitted</p>
                            <p className="font-medium">
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
          </CardContent>
        </Card>
      )}

      {/* Timeline */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-base">Timeline</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <TimelineEntry
              label="Discovered"
              time={job.discovered_at}
              icon={
                <div className="h-2.5 w-2.5 rounded-full bg-blue-500" />
              }
            />
            {job.bids.length > 0 && (
              <TimelineEntry
                label={`Bid submitted ($${job.bids[0].bid_amount})`}
                time={job.bids[0].created_at}
                icon={
                  <div className="h-2.5 w-2.5 rounded-full bg-amber-500" />
                }
              />
            )}
            {job.status === "in_progress" && (
              <TimelineEntry
                label="Pipeline A running"
                icon={
                  <div className="h-2.5 w-2.5 rounded-full bg-emerald-500 animate-pulse" />
                }
              />
            )}
            {job.status === "completed" && (
              <TimelineEntry
                label="Completed"
                icon={
                  <div className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
                }
              />
            )}
            {job.status === "disqualified" && (
              <TimelineEntry
                label={`Disqualified: ${job.disqualify_reason ?? ""}`}
                icon={
                  <div className="h-2.5 w-2.5 rounded-full bg-rose-500" />
                }
              />
            )}
          </div>
        </CardContent>
      </Card>
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
        <p className="text-sm text-foreground">{label}</p>
        {time && (
          <p className="text-xs text-muted-foreground">
            {new Date(time).toLocaleString()}
          </p>
        )}
      </div>
    </div>
  );
}
