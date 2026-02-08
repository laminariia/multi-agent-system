import { Link } from "@remix-run/react";
import { Badge } from "~/components/ui/badge";
import { Progress } from "~/components/ui/progress";
import type { Job } from "~/lib/types";
import { relativeTime, cn } from "~/lib/utils";

const platformIcons: Record<string, string> = {
  freelancer: "FL",
  upwork: "UW",
  fl_ru: "FL.ru",
  kwork: "KW",
};

const statusConfig: Record<string, { label: string; className: string }> = {
  discovered: { label: "New", className: "bg-primary/15 text-primary border-primary/30" },
  qualified: { label: "Qualified", className: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" },
  bid_sent: { label: "Bid Sent", className: "bg-amber-500/15 text-amber-400 border-amber-500/30" },
  in_progress: { label: "In Progress", className: "bg-primary/15 text-primary border-primary/30" },
  completed: { label: "Completed", className: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" },
  disqualified: { label: "Disqualified", className: "bg-muted text-muted-foreground border-muted-foreground/30" },
};

interface JobCardProps {
  job: Job;
  compact?: boolean;
}

export function JobCard({ job, compact }: JobCardProps) {
  const status = statusConfig[job.status] ?? statusConfig.discovered;
  const scorePercent = job.score != null ? Math.round(job.score * 100) : null;
  const budgetText =
    job.budget_min != null && job.budget_max != null
      ? `$${job.budget_min}–$${job.budget_max}`
      : job.budget_min != null
      ? `$${job.budget_min}+`
      : job.budget_max != null
      ? `Up to $${job.budget_max}`
      : null;

  if (compact) {
    return (
      <Link
        to={`/jobs/${job.id}`}
        className="flex items-center gap-4 px-4 py-3 hover:bg-accent/30 rounded-md transition-colors"
      >
        <span className="flex h-8 w-8 items-center justify-center rounded bg-secondary text-[10px] font-bold text-muted-foreground shrink-0">
          {platformIcons[job.platform] ?? job.platform.slice(0, 2).toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium truncate">{job.title}</p>
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground mt-0.5">
            {budgetText && <span>{budgetText}</span>}
            <span>{relativeTime(job.discovered_at)}</span>
          </div>
        </div>
        {scorePercent != null && (
          <div className="w-16 shrink-0">
            <Progress value={scorePercent} className="h-1.5" />
            <p className="text-[10px] text-muted-foreground text-center mt-0.5">
              {scorePercent}%
            </p>
          </div>
        )}
        <Badge variant="outline" className={cn("text-[10px] shrink-0", status.className)}>
          {status.label}
        </Badge>
      </Link>
    );
  }

  return (
    <Link
      to={`/jobs/${job.id}`}
      className="block rounded-lg border border-border/50 p-4 hover:border-primary/30 hover:shadow-lg hover:shadow-primary/5 transition-all animate-fade-in"
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded bg-secondary text-[10px] font-bold text-muted-foreground">
            {platformIcons[job.platform] ?? job.platform.slice(0, 2).toUpperCase()}
          </span>
          <div className="min-w-0">
            <p className="text-sm font-medium leading-tight line-clamp-1">{job.title}</p>
            {budgetText && (
              <p className="text-xs text-muted-foreground mt-0.5">{budgetText} {job.currency}</p>
            )}
          </div>
        </div>
        <Badge variant="outline" className={cn("text-[10px] shrink-0", status.className)}>
          {status.label}
        </Badge>
      </div>

      {job.skills_required && job.skills_required.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-3">
          {job.skills_required.slice(0, 5).map((skill) => (
            <span
              key={skill}
              className="rounded bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground"
            >
              {skill}
            </span>
          ))}
          {job.skills_required.length > 5 && (
            <span className="text-[10px] text-muted-foreground">
              +{job.skills_required.length - 5}
            </span>
          )}
        </div>
      )}

      <div className="flex items-center justify-between">
        {scorePercent != null && (
          <div className="flex items-center gap-2 flex-1 mr-3">
            <Progress value={scorePercent} className="h-1.5 flex-1 max-w-[100px]" />
            <span className="text-[10px] text-muted-foreground">{scorePercent}%</span>
          </div>
        )}
        <span className="text-[10px] text-muted-foreground">
          {relativeTime(job.discovered_at)}
        </span>
      </div>
    </Link>
  );
}
