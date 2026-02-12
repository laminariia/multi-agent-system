import { Link } from "@remix-run/react";
import { Badge } from "~/components/ui/badge";
import { Progress } from "~/components/ui/progress";
import type { Job } from "~/lib/types";
import type { BadgeProps } from "~/components/ui/badge";
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

const currencySymbols: Record<string, string> = {
  USD: "$",
  EUR: "\u20AC",
  GBP: "\u00A3",
  RUB: "\u20BD",
};

function scoreVariant(score: number): BadgeProps["variant"] {
  if (score >= 80) return "success";
  if (score >= 50) return "warning";
  return "secondary";
}

function formatBudget(
  min: number | null,
  max: number | null,
  currency: string,
): string | null {
  if (min == null && max == null) return null;
  const sym = currencySymbols[currency] ?? "";
  const suffix = sym ? "" : ` ${currency}`;
  const fmt = (n: number) => n.toLocaleString("en-US");
  if (min != null && max != null) {
    return `${sym}${fmt(min)}\u2013${sym}${fmt(max)}${suffix}`;
  }
  if (min != null) {
    return `${sym}${fmt(min)}+${suffix}`;
  }
  return `Up to ${sym}${fmt(max!)}${suffix}`;
}

interface JobCardProps {
  job: Job;
  compact?: boolean;
}

export function JobCard({ job, compact }: JobCardProps) {
  const status = statusConfig[job.status] ?? statusConfig.discovered;
  const scorePercent = job.score != null ? Math.round(job.score * 100) : null;
  const budgetText = formatBudget(job.budget_min, job.budget_max, job.currency);

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
          <Badge variant={scoreVariant(scorePercent)} className="text-[10px] shrink-0">
            Score: {scorePercent}
          </Badge>
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
      {/* Row 1: Platform icon + title/budget | status badge */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <span className="flex h-8 w-8 items-center justify-center rounded bg-secondary text-[10px] font-bold text-muted-foreground shrink-0">
            {platformIcons[job.platform] ?? job.platform.slice(0, 2).toUpperCase()}
          </span>
          <div className="min-w-0">
            <p className="text-sm font-medium leading-tight line-clamp-1">{job.title}</p>
            {budgetText && (
              <p className="text-xs text-muted-foreground mt-0.5">{budgetText}</p>
            )}
          </div>
        </div>
        <Badge variant="outline" className={cn("text-[10px] shrink-0 ml-2", status.className)}>
          {status.label}
        </Badge>
      </div>

      {/* Row 2: Skill badges (first 3) */}
      {job.skills_required && job.skills_required.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-3">
          {job.skills_required.slice(0, 3).map((skill) => (
            <Badge
              key={skill}
              variant="secondary"
              className="px-1.5 py-0 text-[10px] font-normal"
            >
              {skill}
            </Badge>
          ))}
          {job.skills_required.length > 3 && (
            <span className="text-[10px] text-muted-foreground self-center">
              +{job.skills_required.length - 3}
            </span>
          )}
        </div>
      )}

      {/* Row 3: Score badge + progress | discovered time */}
      <div className="flex items-center justify-between">
        {scorePercent != null ? (
          <div className="flex items-center gap-2 flex-1 mr-3">
            <Badge variant={scoreVariant(scorePercent)} className="text-[10px] shrink-0">
              Score: {scorePercent}
            </Badge>
            <Progress value={scorePercent} className="h-1.5 flex-1 max-w-[80px]" />
          </div>
        ) : (
          <span />
        )}
        <span className="text-[10px] text-muted-foreground shrink-0">
          {relativeTime(job.discovered_at)}
        </span>
      </div>
    </Link>
  );
}
