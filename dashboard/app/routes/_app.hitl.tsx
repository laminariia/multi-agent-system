import { useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "~/components/ui/badge";
import { Input } from "~/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Skeleton } from "~/components/ui/skeleton";
import { HITLCard } from "~/components/hitl-card";
import { Pagination } from "~/components/pagination";
import { Button } from "~/components/ui/button";
import { fetchHITLPending, resolveHITL, bulkResolveHITL, fetchHITLStats } from "~/lib/api";
import { downloadCSV } from "~/lib/utils";
import { toast } from "~/hooks/use-toast";
import { useAuthStore } from "~/stores/auth-store";

const PAGE_SIZE = 24;

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
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [page, setPage] = useState(0);
  const [bulkMode, setBulkMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkLoading, setBulkLoading] = useState(false);
  const user = useAuthStore((s) => s.user);

  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(searchQuery), 300);
    return () => clearTimeout(id);
  }, [searchQuery]);

  useEffect(() => { setPage(0); }, [debouncedSearch]);

  const handleFilterChange = (v: string) => { setActiveFilter(v); setPage(0); };

  const { data, isLoading, error } = useQuery({
    queryKey: [
      "hitl-pending",
      activeFilter === "all" ? undefined : activeFilter,
      debouncedSearch || undefined,
      page,
    ],
    queryFn: () =>
      fetchHITLPending({
        type: activeFilter === "all" ? undefined : activeFilter,
        search: debouncedSearch || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
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

  const toggleSelect = (id: string, selected: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (selected) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  };

  const handleBulkAction = async (action: string) => {
    if (selectedIds.size === 0) return;
    setBulkLoading(true);
    try {
      const result = await bulkResolveHITL(Array.from(selectedIds), action);
      const parts: string[] = [];
      if (result.resolved > 0) parts.push(`${result.resolved} resolved`);
      if (result.failed > 0) parts.push(`${result.failed} failed`);
      toast({
        title: `Bulk ${action}`,
        description: parts.join(", ") || "Done",
        variant: result.failed > 0 && result.resolved === 0 ? "destructive" : "success",
      });
      setSelectedIds(new Set());
      queryClient.invalidateQueries({ queryKey: ["hitl-pending"] });
      queryClient.invalidateQueries({ queryKey: ["hitl-pending-count"] });
      queryClient.invalidateQueries({ queryKey: ["hitl-stats"] });
    } catch (err) {
      toast({
        title: "Bulk action failed",
        description: err instanceof Error ? err.message : "Something went wrong",
        variant: "destructive",
      });
    } finally {
      setBulkLoading(false);
    }
  };

  const handleToggleBulkMode = () => {
    setBulkMode((prev) => {
      if (prev) setSelectedIds(new Set());
      return !prev;
    });
  };

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const urgentCount = data?.pending_urgent ?? 0;

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
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

        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 w-full sm:w-auto">
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
          <Button
            variant="outline"
            size="sm"
            disabled={items.length === 0}
            onClick={() => {
              const rows = items.map((i) => ({
                title: i.title,
                type: i.type,
                priority: i.priority,
                description: i.description ?? "",
                created_at: i.created_at,
                expires_at: i.expires_at ?? "",
              }));
              downloadCSV(
                rows,
                `hitl-${new Date().toISOString().slice(0, 10)}.csv`
              );
            }}
          >
            Export CSV
          </Button>
        </div>
      </div>

      {/* Filter tabs + search */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 sm:gap-4">
        <Tabs
          value={activeFilter}
          onValueChange={handleFilterChange}
        >
          <TabsList>
            {filterTabs.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <Input
          placeholder="Search by title..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full sm:w-[220px]"
        />
        <Button
          variant={bulkMode ? "default" : "outline"}
          size="sm"
          onClick={handleToggleBulkMode}
        >
          {bulkMode ? "Exit Bulk" : "Bulk Select"}
        </Button>
      </div>

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
              userRole={user?.role}
              selected={bulkMode ? selectedIds.has(item.id) : undefined}
              onSelect={bulkMode ? toggleSelect : undefined}
            />
          ))}
        </div>
      )}
      {/* Pagination */}
      <Pagination
        page={page}
        pageSize={PAGE_SIZE}
        total={total}
        onPageChange={setPage}
      />

      {/* Bulk action bar */}
      {bulkMode && selectedIds.size > 0 && (
        <div className="fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-background p-4 shadow-lg">
          <div className="mx-auto flex flex-col sm:flex-row max-w-screen-xl items-center justify-between gap-3 sm:gap-4">
            <span className="text-sm font-medium text-foreground">
              {selectedIds.size} selected
            </span>
            <div className="flex flex-wrap items-center justify-center gap-2 w-full sm:w-auto">
              <Button
                variant="success"
                size="sm"
                disabled={bulkLoading}
                onClick={() => handleBulkAction("approve")}
                className="flex-1 sm:flex-none"
              >
                Approve All
              </Button>
              <Button
                variant="destructive"
                size="sm"
                disabled={bulkLoading}
                onClick={() => handleBulkAction("reject")}
                className="flex-1 sm:flex-none"
              >
                Reject All
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={bulkLoading}
                onClick={() => handleBulkAction("skip")}
                className="flex-1 sm:flex-none"
              >
                Skip All
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setSelectedIds(new Set())}
                className="flex-1 sm:flex-none"
              >
                Clear
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
