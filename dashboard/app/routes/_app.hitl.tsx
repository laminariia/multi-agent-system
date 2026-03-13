import { useState, useEffect, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Input } from "~/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Skeleton } from "~/components/ui/skeleton";
import { HITLCard } from "~/components/hitl-card";
import { Pagination } from "~/components/pagination";
import { Button } from "~/components/ui/button";
import { Textarea } from "~/components/ui/textarea";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "~/components/ui/sheet";
import { AnimatePresence, motion } from "framer-motion";
import { fetchHITLPending, resolveHITL, bulkResolveHITL, fetchHITLStats } from "~/lib/api";
import { downloadCSV } from "~/lib/utils";
import { toast } from "~/hooks/use-toast";
import { useAuthStore } from "~/stores/auth-store";
import type { HITLItem } from "~/lib/types";

const PAGE_SIZE = 24;

const HITL_TABS = [
  { value: "all", label: "All" },
  { value: "bid_approval", label: "Bids" },
  { value: "dev_launch", label: "Dev Launch" },
  { value: "plan_review", label: "Plans" },
  { value: "critic_escalation", label: "Critic" },
  { value: "partial_failure", label: "Failures" },
  { value: "final_delivery", label: "Deliveries" },
  { value: "lead_card", label: "Leads" },
  { value: "concept_review", label: "Concepts" },
  { value: "concept_approved", label: "Approved" },
  { value: "design_review", label: "Design" },
  { value: "email_approval", label: "Emails" },
  { value: "portfolio_review", label: "Portfolio" },
] as const;

/* ------------------------------------------------------------------ */
/*  CountdownTimer — live countdown for expires_at                     */
/* ------------------------------------------------------------------ */

function CountdownTimer({ expiresAt }: { expiresAt: string }) {
  const [remaining, setRemaining] = useState("");

  useEffect(() => {
    const update = () => {
      const diff = new Date(expiresAt).getTime() - Date.now();
      if (diff <= 0) {
        setRemaining("EXPIRED");
        return;
      }
      const h = Math.floor(diff / 3_600_000);
      const m = Math.floor((diff % 3_600_000) / 60_000);
      setRemaining(`${h}h ${m}m`);
    };
    update();
    const interval = setInterval(update, 60_000);
    return () => clearInterval(interval);
  }, [expiresAt]);

  const isExpired = remaining === "EXPIRED";

  return (
    <span
      className={`text-xs font-mono ${isExpired ? "text-red-400" : "text-orange-400"}`}
    >
      {remaining}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  DrawerPayloadView — renders payload key/values in the sheet        */
/* ------------------------------------------------------------------ */

function DrawerPayloadView({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload).filter(
    ([, v]) => v != null && v !== ""
  );
  if (entries.length === 0) {
    return (
      <p className="text-xs text-zinc-500 italic">No payload data available.</p>
    );
  }
  return (
    <div className="space-y-2">
      {entries.map(([key, value]) => (
        <div key={key} className="flex flex-col gap-0.5">
          <span className="text-xs font-medium text-zinc-400 capitalize">
            {key.replace(/_/g, " ")}
          </span>
          {typeof value === "object" ? (
            <pre className="text-xs text-zinc-300 bg-zinc-900 rounded p-2 overflow-x-auto max-h-40 whitespace-pre-wrap">
              {JSON.stringify(value, null, 2)}
            </pre>
          ) : (
            <span className="text-sm text-zinc-200">{String(value)}</span>
          )}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  DetailDrawer — right-side sheet with full item details             */
/* ------------------------------------------------------------------ */

const ACTION_VARIANTS: Record<
  string,
  "default" | "success" | "warning" | "destructive" | "outline"
> = {
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

function DetailDrawer({
  item,
  open,
  onOpenChange,
  onResolve,
}: {
  item: HITLItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onResolve: (id: string, action: string, notes?: string) => Promise<void>;
}) {
  const [notes, setNotes] = useState("");
  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  // Reset notes when a different item is opened
  useEffect(() => {
    if (item) setNotes("");
  }, [item?.id]);

  const handleAction = useCallback(
    async (action: string) => {
      if (!item) return;
      setLoadingAction(action);
      try {
        await onResolve(item.id, action, notes || undefined);
        onOpenChange(false);
      } finally {
        setLoadingAction(null);
      }
    },
    [item, notes, onResolve, onOpenChange]
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full sm:w-[480px] overflow-y-auto bg-zinc-950 border-zinc-800"
      >
        {item && (
          <>
            <SheetHeader>
              <div className="flex items-center gap-2">
                <Badge
                  variant={item.priority === "urgent" ? "destructive" : "secondary"}
                  className="text-[10px] capitalize"
                >
                  {item.priority}
                </Badge>
                <span className="text-xs text-zinc-400 capitalize">
                  {item.type.replace(/_/g, " ")}
                </span>
                {item.expires_at && (
                  <CountdownTimer expiresAt={item.expires_at} />
                )}
              </div>
              <SheetTitle>{item.title}</SheetTitle>
              {item.description && (
                <SheetDescription>{item.description}</SheetDescription>
              )}
            </SheetHeader>

            {/* Timestamps */}
            <div className="flex items-center gap-4 text-xs text-zinc-500 mt-2">
              <span>
                Created:{" "}
                {new Date(item.created_at).toLocaleString()}
              </span>
              {item.expires_at && (
                <span>
                  Expires:{" "}
                  {new Date(item.expires_at).toLocaleString()}
                </span>
              )}
            </div>

            {/* Payload details */}
            <div className="mt-4">
              <h4 className="text-sm font-medium text-zinc-300 mb-2">
                Details
              </h4>
              <div className="rounded-lg border border-zinc-800 p-3 bg-zinc-900/50">
                <DrawerPayloadView payload={item.payload} />
              </div>
            </div>

            {/* Operator notes */}
            <div className="mt-4">
              <label
                htmlFor="operator-notes"
                className="text-sm font-medium text-zinc-300 mb-1.5 block"
              >
                Operator Notes
              </label>
              <Textarea
                id="operator-notes"
                placeholder="Add notes before resolving..."
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                className="bg-zinc-900 border-zinc-700 text-zinc-200 placeholder:text-zinc-600 min-h-[80px] resize-none"
              />
            </div>

            {/* Action buttons */}
            <div className="mt-6 flex flex-wrap gap-2">
              {item.available_actions.map((action) => (
                <Button
                  key={action}
                  variant={ACTION_VARIANTS[action] ?? "outline"}
                  size="sm"
                  disabled={loadingAction !== null}
                  onClick={() => handleAction(action)}
                  className={`capitalize ${action === "approve" ? "bg-orange-500 hover:bg-orange-600 text-white border-0" : ""}`}
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
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

/* ------------------------------------------------------------------ */
/*  Main HITLPage                                                      */
/* ------------------------------------------------------------------ */

export default function HITLPage() {
  const queryClient = useQueryClient();
  const [activeFilter, setActiveFilter] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [page, setPage] = useState(0);
  const [bulkMode, setBulkMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkLoading, setBulkLoading] = useState(false);
  const [drawerItem, setDrawerItem] = useState<HITLItem | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
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
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  const { data: stats } = useQuery({
    queryKey: ["hitl-stats"],
    queryFn: fetchHITLStats,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const invalidateHITL = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["hitl-pending"] });
    queryClient.invalidateQueries({ queryKey: ["hitl-pending-count"] });
    queryClient.invalidateQueries({ queryKey: ["hitl-stats"] });
  }, [queryClient]);

  const handleResolve = useCallback(async (id: string, action: string, _notes?: string) => {
    try {
      const result = await resolveHITL(id, action);
      toast({
        title: "Action completed",
        description: `${action} - ${result.next_action || "Done"}`,
        variant: "success",
      });
      invalidateHITL();
    } catch (err) {
      toast({
        title: "Action failed",
        description:
          err instanceof Error ? err.message : "Something went wrong",
        variant: "destructive",
      });
    }
  }, [invalidateHITL]);

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
      invalidateHITL();
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

  const handleCardClick = (item: HITLItem) => {
    if (bulkMode) return;
    setDrawerItem(item);
    setDrawerOpen(true);
  };

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const urgentCount = data?.pending_urgent ?? 0;

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-white">
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
            <div className="flex items-center gap-4 text-sm text-zinc-400">
              <span>
                Today: {stats.today.resolved} resolved
              </span>
              <span className="text-zinc-700">|</span>
              <span>
                Avg: {stats.avg_resolution_time_minutes.toFixed(0)}m
              </span>
              {stats.today.expired > 0 && (
                <>
                  <span className="text-zinc-700">|</span>
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
          <TabsList className="flex-wrap h-auto gap-1">
            {HITL_TABS.map((tab) => (
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
          className="w-full sm:w-[220px] bg-zinc-900 border-zinc-700"
        />
        <Button
          variant={bulkMode ? "default" : "outline"}
          size="sm"
          onClick={handleToggleBulkMode}
          className={bulkMode ? "bg-orange-500 hover:bg-orange-600 text-white border-0" : "border-zinc-700"}
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
          <p className="text-white text-lg font-medium">
            All clear
          </p>
          <p className="text-zinc-400 text-sm mt-1 max-w-sm">
            No pending items in the queue. You'll be notified when something needs your attention.
          </p>
        </div>
      )}

      {/* Items list with framer-motion animations */}
      {items.length > 0 && (
        <div className="space-y-2">
          <AnimatePresence mode="popLayout">
            {items.map((item) => {
              const priorityBorder =
                item.priority === "urgent" ? "border-l-rose-500" :
                item.priority === "normal" ? "border-l-amber-500" :
                "border-l-blue-500";
              return (
                <motion.div
                  key={item.id}
                  layout
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, x: 100, scale: 0.95 }}
                  transition={{ duration: 0.2 }}
                >
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => handleCardClick(item)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        handleCardClick(item);
                      }
                    }}
                    className={`flex items-center gap-4 rounded-lg border border-zinc-800 border-l-4 px-4 py-3 bg-zinc-900 cursor-pointer hover:bg-zinc-800/70 transition-colors ${priorityBorder} ${bulkMode && selectedIds.has(item.id) ? "bg-zinc-800" : ""}`}
                  >
                    {bulkMode && (
                      <input
                        type="checkbox"
                        checked={selectedIds.has(item.id)}
                        onChange={(e) => {
                          e.stopPropagation();
                          toggleSelect(item.id, e.target.checked);
                        }}
                        onClick={(e) => e.stopPropagation()}
                        className="h-4 w-4 shrink-0"
                      />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 mb-0.5">
                        <Badge variant={item.priority === "urgent" ? "destructive" : "secondary"} className="text-[10px] capitalize">
                          {item.priority}
                        </Badge>
                        <span className="text-xs text-zinc-400 capitalize">{item.type.replace(/_/g, " ")}</span>
                      </div>
                      <p className="text-sm font-medium truncate text-zinc-200">{item.title}</p>
                      {item.description && (
                        <p className="text-xs text-zinc-500 truncate mt-0.5">{item.description}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-3 shrink-0">
                      {item.expires_at && (
                        <div className="hidden sm:flex items-center gap-1.5">
                          <svg
                            className="h-3 w-3 text-zinc-500"
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
                          <CountdownTimer expiresAt={item.expires_at} />
                        </div>
                      )}
                      {!bulkMode && (
                        <div className="flex items-center gap-1.5">
                          {item.available_actions.slice(0, 3).map((action) => (
                            <Button
                              key={action}
                              variant={action === "reject" ? "destructive" : "outline"}
                              size="sm"
                              className={`h-7 text-xs capitalize ${action === "approve" ? "bg-orange-500 hover:bg-orange-600 text-white border-0" : "border-zinc-700"}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                handleResolve(item.id, action);
                              }}
                            >
                              {action}
                            </Button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>
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
        <div className="fixed bottom-0 left-0 right-0 z-50 border-t border-zinc-800 bg-zinc-950 p-4 shadow-lg">
          <div className="mx-auto flex flex-col sm:flex-row max-w-screen-xl items-center justify-between gap-3 sm:gap-4">
            <span className="text-sm font-medium text-white">
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

      {/* Detail Drawer */}
      <DetailDrawer
        item={drawerItem}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        onResolve={handleResolve}
      />
    </div>
  );
}
