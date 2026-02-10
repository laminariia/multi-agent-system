import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
} from "~/components/ui/card";
import { Skeleton } from "~/components/ui/skeleton";
import { fetchHITLPending, resolveHITL } from "~/lib/api";
import { toast } from "~/hooks/use-toast";
import { relativeTime } from "~/lib/utils";
import type { HITLItem } from "~/lib/types";

export default function OutreachPage() {
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["outreach-pending"],
    queryFn: () =>
      fetchHITLPending({
        type: "email_approval",
        limit: 50,
      }),
    refetchInterval: 15_000,
  });

  const handleResolve = async (id: string, action: string) => {
    try {
      const result = await resolveHITL(id, action);
      toast({
        title: "Action completed",
        description: `${action} - ${result.next_action || "Done"}`,
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["outreach-pending"] });
      queryClient.invalidateQueries({ queryKey: ["hitl-pending"] });
      queryClient.invalidateQueries({ queryKey: ["hitl-pending-count"] });
    } catch (err) {
      toast({
        title: "Action failed",
        description:
          err instanceof Error ? err.message : "Something went wrong",
        variant: "destructive",
      });
    }
  };

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            Outreach Review
          </h1>
          {total > 0 && (
            <Badge variant="secondary" className="text-sm">
              {total} pending
            </Badge>
          )}
        </div>

        <p className="text-sm text-muted-foreground">
          Review and approve cold email drafts before sending
        </p>
      </div>

      {/* Loading state */}
      {isLoading && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-[240px]" />
          ))}
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load outreach items:{" "}
          {error instanceof Error ? error.message : "Unknown error"}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !error && items.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-center animate-fade-in">
          <div className="rounded-full bg-success/10 p-4 mb-4">
            <svg
              className="h-8 w-8 text-success"
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
              <polyline points="22 4 12 14.01 9 11.01" />
            </svg>
          </div>
          <p className="text-foreground text-lg font-medium">
            No emails to review
          </p>
          <p className="text-muted-foreground text-sm mt-1 max-w-sm">
            When the Outreach Agent generates cold emails, they will appear here
            for your approval before sending.
          </p>
        </div>
      )}

      {/* Email cards */}
      {items.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((item) => (
            <OutreachCard
              key={item.id}
              item={item}
              onResolve={handleResolve}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function OutreachCard({
  item,
  onResolve,
}: {
  item: HITLItem;
  onResolve: (id: string, action: string) => Promise<void>;
}) {
  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  const handleAction = async (action: string) => {
    setLoadingAction(action);
    try {
      await onResolve(item.id, action);
    } finally {
      setLoadingAction(null);
    }
  };

  const payload = item.payload ?? {};

  return (
    <Card className="relative overflow-hidden border-border/50 hover:border-primary/30 transition-colors animate-fade-in">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <Badge variant="bid">Email</Badge>
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

        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          {payload.city && (
            <div>
              <span className="text-muted-foreground">City: </span>
              <span className="text-foreground/90">{payload.city}</span>
            </div>
          )}
          {payload.email_count != null && (
            <div>
              <span className="text-muted-foreground">Emails: </span>
              <span className="text-foreground/90">{payload.email_count}</span>
            </div>
          )}
          {payload.campaign_id && (
            <div className="col-span-2">
              <span className="text-muted-foreground">Campaign: </span>
              <span className="text-foreground/90 font-mono text-[10px]">
                {payload.campaign_id}
              </span>
            </div>
          )}
        </div>
      </CardContent>

      <CardFooter className="gap-2 flex-wrap">
        {item.available_actions.map((action) => (
          <Button
            key={action}
            variant={
              action === "approve"
                ? "success"
                : action === "reject"
                ? "destructive"
                : "outline"
            }
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
        ))}
      </CardFooter>
    </Card>
  );
}
