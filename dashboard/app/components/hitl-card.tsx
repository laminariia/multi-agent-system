import { useState } from "react";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
} from "~/components/ui/card";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import type { HITLItem } from "~/lib/types";
import { relativeTime, formatCountdown } from "~/lib/utils";

const typeConfig: Record<
  HITLItem["type"],
  { label: string; variant: "bid" | "review" | "delivery" | "alert" | "revision" | "scope" | "plan" }
> = {
  bid_approval: { label: "Bid", variant: "bid" },
  code_review: { label: "Review", variant: "review" },
  delivery: { label: "Delivery", variant: "delivery" },
  scope_creep: { label: "Scope", variant: "scope" },
  plan_review: { label: "Plan", variant: "plan" },
  alert: { label: "Alert", variant: "alert" },
  revision: { label: "Revision", variant: "revision" },
  email_approval: { label: "Email", variant: "bid" },
  final_review: { label: "Final", variant: "delivery" },
  job_review: { label: "Job", variant: "review" },
};

const actionVariants: Record<string, "default" | "success" | "warning" | "destructive" | "outline"> = {
  approve: "success",
  accept: "success",
  submit: "success",
  skip: "outline",
  reject: "destructive",
  later: "warning",
  dismiss: "outline",
  escalate: "warning",
  acknowledge: "default",
  retry: "default",
};

interface HITLCardProps {
  item: HITLItem;
  onResolve: (id: string, action: string) => Promise<void>;
  userRole?: string;
  selected?: boolean;
  onSelect?: (id: string, selected: boolean) => void;
}

export function HITLCard({ item, onResolve, userRole, selected, onSelect }: HITLCardProps) {
  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const config = typeConfig[item.type] ?? typeConfig.alert;
  const canResolve = !userRole || userRole === "owner" || userRole === "co_owner";

  const handleAction = async (action: string) => {
    setLoadingAction(action);
    try {
      await onResolve(item.id, action);
    } finally {
      setLoadingAction(null);
    }
  };

  return (
    <Card className={`relative overflow-hidden border-border/50 hover:border-primary/30 transition-colors animate-fade-in ${selected ? "ring-2 ring-primary border-primary/50" : ""}`}>
      {/* Priority indicator stripe */}
      {item.priority === "urgent" && (
        <div className="absolute top-0 left-0 right-0 h-0.5 bg-red-500" />
      )}

      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            {onSelect && (
              <input
                type="checkbox"
                checked={selected ?? false}
                onChange={(e) => onSelect(item.id, e.target.checked)}
                className="h-4 w-4 rounded border-border accent-primary flex-shrink-0 cursor-pointer"
                aria-label={`Select ${item.title}`}
              />
            )}
            {item.priority === "urgent" && (
              <span className="flex-shrink-0 h-2 w-2 rounded-full bg-red-500 animate-pulse-dot" />
            )}
            <Badge variant={config.variant}>{config.label}</Badge>
          </div>
          <span className="text-xs text-muted-foreground flex-shrink-0">
            {relativeTime(item.created_at)}
          </span>
        </div>
        <h3 className="text-sm font-semibold text-foreground mt-2 leading-tight">
          {item.title}
        </h3>
      </CardHeader>

      <CardContent className="pb-3">
        {item.description && (
          <p className="text-xs text-muted-foreground mb-3 line-clamp-2">
            {item.description}
          </p>
        )}

        {/* Type-specific payload details */}
        <PayloadDetails type={item.type} payload={item.payload} />

        {/* Expiry countdown */}
        {item.expires_at && (
          <div className="mt-3 flex items-center gap-1.5">
            <svg
              className="h-3 w-3 text-muted-foreground"
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            <span className="text-xs text-muted-foreground">
              Expires {formatCountdown(item.expires_at)}
            </span>
          </div>
        )}
      </CardContent>

      <CardFooter className="gap-2 flex-wrap">
        {canResolve ? (
          item.available_actions.map((action) => (
            <Button
              key={action}
              variant={actionVariants[action] ?? "outline"}
              size="sm"
              disabled={loadingAction !== null}
              onClick={() => handleAction(action)}
              className="text-xs capitalize"
            >
              {loadingAction === action ? (
                <span className="flex items-center gap-1.5">
                  <svg
                    className="h-3 w-3 animate-spin"
                    xmlns="http://www.w3.org/2000/svg"
                    fill="none"
                    viewBox="0 0 24 24"
                  >
                    <circle
                      className="opacity-25"
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="4"
                    />
                    <path
                      className="opacity-75"
                      fill="currentColor"
                      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                    />
                  </svg>
                  {action}
                </span>
              ) : (
                action
              )}
            </Button>
          ))
        ) : (
          <Badge variant="secondary" className="text-xs">
            Awaiting owner approval
          </Badge>
        )}
      </CardFooter>
    </Card>
  );
}

function PayloadDetails({
  type,
  payload,
}: {
  type: HITLItem["type"];
  payload: Record<string, any>;
}) {
  switch (type) {
    case "bid_approval":
      return (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          {payload.platform && (
            <Detail label="Platform" value={payload.platform} />
          )}
          {payload.bid_amount != null && (
            <Detail label="Bid Amount" value={`$${payload.bid_amount}`} />
          )}
          {payload.client_rating != null && (
            <Detail
              label="Client Rating"
              value={`${payload.client_rating}/5`}
            />
          )}
          {payload.proposal_preview && (
            <div className="col-span-2 mt-1">
              <span className="text-muted-foreground">Proposal: </span>
              <span className="text-foreground/80 line-clamp-2">
                {payload.proposal_preview}
              </span>
            </div>
          )}
        </div>
      );

    case "code_review":
      return (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          {payload.quality_score != null && (
            <Detail
              label="Quality"
              value={`${payload.quality_score}/10`}
              valueClass={
                payload.quality_score >= 7
                  ? "text-emerald-400"
                  : payload.quality_score >= 4
                  ? "text-amber-400"
                  : "text-red-400"
              }
            />
          )}
          {payload.security_issues != null && (
            <Detail
              label="Security Issues"
              value={payload.security_issues}
              valueClass={
                payload.security_issues > 0 ? "text-red-400" : "text-emerald-400"
              }
            />
          )}
          {payload.test_coverage != null && (
            <Detail
              label="Test Coverage"
              value={`${payload.test_coverage}%`}
            />
          )}
        </div>
      );

    case "delivery":
      return (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          {payload.client_name && (
            <Detail label="Client" value={payload.client_name} />
          )}
          {payload.deadline && (
            <Detail
              label="Deadline"
              value={relativeTime(payload.deadline)}
            />
          )}
          {payload.deliverables_count != null && (
            <Detail
              label="Deliverables"
              value={payload.deliverables_count}
            />
          )}
        </div>
      );

    case "alert":
      return (
        <div className="text-xs space-y-1">
          {payload.severity && (
            <Detail
              label="Severity"
              value={payload.severity}
              valueClass={
                payload.severity === "critical"
                  ? "text-red-400 font-medium"
                  : payload.severity === "warning"
                  ? "text-amber-400"
                  : "text-muted-foreground"
              }
            />
          )}
          {payload.details && (
            <p className="text-muted-foreground mt-1 line-clamp-3">
              {payload.details}
            </p>
          )}
        </div>
      );

    case "revision":
      return (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          {payload.client_name && (
            <Detail label="Client" value={payload.client_name} />
          )}
          {payload.revision_number != null && (
            <Detail label="Revision #" value={payload.revision_number} />
          )}
          {payload.feedback && (
            <div className="col-span-2 mt-1">
              <span className="text-muted-foreground">Feedback: </span>
              <span className="text-foreground/80 line-clamp-2">
                {payload.feedback}
              </span>
            </div>
          )}
        </div>
      );

    default:
      return null;
  }
}

function Detail({
  label,
  value,
  valueClass,
}: {
  label: string;
  value: React.ReactNode;
  valueClass?: string;
}) {
  return (
    <div>
      <span className="text-muted-foreground">{label}: </span>
      <span className={valueClass ?? "text-foreground/90"}>{value}</span>
    </div>
  );
}
