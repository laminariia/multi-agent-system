import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "~/components/ui/badge";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Skeleton } from "~/components/ui/skeleton";
import { HITLCard } from "~/components/hitl-card";
import { fetchHITLPending, resolveHITL, fetchHITLStats } from "~/lib/api";
import { toast } from "~/hooks/use-toast";

const filterTabs = [
  { value: "all", label: "All" },
  { value: "bid_approval", label: "Bids" },
  { value: "code_review", label: "Reviews" },
  { value: "delivery", label: "Deliveries" },
  { value: "scope_creep", label: "Scope" },
  { value: "plan_review", label: "Plans" },
  { value: "alert", label: "Alerts" },
] as const;

export default function HITLPage() {
  const queryClient = useQueryClient();
  const [activeFilter, setActiveFilter] = useState("all");

  const { data, isLoading, error } = useQuery({
    queryKey: [
      "hitl-pending",
      activeFilter === "all" ? undefined : activeFilter,
    ],
    queryFn: () =>
      fetchHITLPending({
        type: activeFilter === "all" ? undefined : activeFilter,
        limit: 50,
      }),
    refetchInterval: 30_000,
  });

  const { data: stats } = useQuery({
    queryKey: ["hitl-stats"],
    queryFn: fetchHITLStats,
    refetchInterval: 60_000,
  });

  const handleResolve = async (id: string, action: string) => {
    try {
      const result = await resolveHITL(id, action);
      toast({
        title: "Action completed",
        description: `${action} - ${result.next_action || "Done"}`,
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["hitl-pending"] });
      queryClient.invalidateQueries({ queryKey: ["hitl-pending-count"] });
      queryClient.invalidateQueries({ queryKey: ["hitl-stats"] });
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
  const urgentCount = data?.pending_urgent ?? 0;

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            HITL Queue
          </h1>
          {total > 0 && (
            <Badge variant="secondary" className="text-sm">
              {total} pending
            </Badge>
          )}
          {urgentCount > 0 && (
            <Badge variant="destructive" className="text-sm">
              {urgentCount} urgent
            </Badge>
          )}
        </div>

        {stats && (
          <div className="flex items-center gap-4 text-sm text-muted-foreground">
            <span>
              Today: {stats.today.resolved} resolved
            </span>
            <span className="text-border">|</span>
            <span>
              Avg: {stats.avg_resolution_time_minutes.toFixed(0)}m
            </span>
            {stats.today.expired > 0 && (
              <>
                <span className="text-border">|</span>
                <span className="text-destructive">
                  {stats.today.expired} expired
                </span>
              </>
            )}
          </div>
        )}
      </div>

      {/* Filter tabs */}
      <Tabs
        value={activeFilter}
        onValueChange={setActiveFilter}
      >
        <TabsList>
          {filterTabs.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {/* Loading state */}
      {isLoading && !data && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-[200px]" />
          ))}
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load HITL items:{" "}
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
            All clear
          </p>
          <p className="text-muted-foreground text-sm mt-1 max-w-sm">
            No pending items in the queue. You'll be notified when something needs your attention.
          </p>
        </div>
      )}

      {/* Items grid */}
      {items.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((item) => (
            <HITLCard
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
