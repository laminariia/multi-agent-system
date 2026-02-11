import { useState } from "react";
import { useParams, Link } from "@remix-run/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Separator } from "~/components/ui/separator";
import { Skeleton } from "~/components/ui/skeleton";
import { Textarea } from "~/components/ui/textarea";
import { fetchJob, disqualifyJob } from "~/lib/api";
import { relativeTime } from "~/lib/utils";
import { toast } from "~/hooks/use-toast";

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [showDisqualify, setShowDisqualify] = useState(false);
  const [disqualifyReason, setDisqualifyReason] = useState("");
  const [disqualifying, setDisqualifying] = useState(false);

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

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-[300px]" />
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
          {job.url && (
            <Button variant="outline" size="sm" asChild>
              <a href={job.url} target="_blank" rel="noopener noreferrer">
                View on Platform
              </a>
            </Button>
          )}
          {job.status !== "disqualified" && (
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
              {job.bids.map((bid) => (
                <div
                  key={bid.id}
                  className="rounded-md border border-border/50 p-4 space-y-2"
                >
                  <div className="flex items-center justify-between">
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
                    <p className="text-xs text-muted-foreground">
                      {relativeTime(bid.created_at)}
                    </p>
                  </div>
                </div>
              ))}
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
                label="Work in progress"
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
