import { useState, useCallback, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Textarea } from "~/components/ui/textarea";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "~/components/ui/sheet";
import { ScrollArea } from "~/components/ui/scroll-area";
import { fetchHITLPending, resolveHITL } from "~/lib/api";
import { relativeTime } from "~/lib/utils";
import { toast } from "~/hooks/use-toast";
import { useWsSubscription } from "~/hooks/use-ws-subscription";
import type { HITLItem } from "~/lib/types";

/* ---------- types ---------- */

interface DesignPayload {
  mockup_url?: string;
  preview_url?: string;
  pencil_project_id?: string;
  revision_number?: number;
  design_notes?: string;
  job_title?: string;
  designer?: string;
}

interface RevisionEntry {
  id: string;
  action: string;
  notes: string | null;
  timestamp: string;
  reviewer: string | null;
}

/* ---------- helpers ---------- */

function extractDesignPayload(item: HITLItem): DesignPayload {
  const p = item.payload ?? {};
  return {
    mockup_url: p.mockup_url as string | undefined,
    preview_url: p.preview_url as string | undefined,
    pencil_project_id: p.pencil_project_id as string | undefined,
    revision_number: p.revision_number as number | undefined,
    design_notes: p.design_notes as string | undefined,
    job_title: p.job_title as string | undefined,
    designer: p.designer as string | undefined,
  };
}

function extractRevisions(item: HITLItem): RevisionEntry[] {
  const revisions = (item.payload?.revisions as RevisionEntry[] | undefined) ?? [];
  return revisions;
}

/* ---------- status badge ---------- */

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  approved: "bg-green-500/20 text-green-400 border-green-500/30",
  rejected: "bg-red-500/20 text-red-400 border-red-500/30",
  changes_requested: "bg-orange-500/20 text-orange-400 border-orange-500/30",
};

/* ---------- mockup preview ---------- */

function MockupPreview({
  url,
  loading,
}: {
  url: string | undefined;
  loading: boolean;
}) {
  if (loading) {
    return <Skeleton className="w-full aspect-video rounded-lg" />;
  }

  if (!url) {
    return (
      <div className="w-full aspect-video bg-zinc-900 border border-zinc-800 rounded-lg flex items-center justify-center">
        <div className="text-center space-y-2">
          <svg
            className="h-12 w-12 mx-auto text-zinc-600"
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
            <circle cx="8.5" cy="8.5" r="1.5" />
            <polyline points="21 15 16 10 5 21" />
          </svg>
          <p className="text-zinc-500 text-sm">No mockup available</p>
          <p className="text-zinc-600 text-xs">
            Design screenshots will appear here when available
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
      <img
        src={url}
        alt="Design mockup"
        className="w-full h-auto object-contain max-h-[60vh]"
        onError={(e) => {
          const target = e.target as HTMLImageElement;
          target.style.display = "none";
          const parent = target.parentElement;
          if (parent) {
            const fallback = document.createElement("div");
            fallback.className =
              "w-full aspect-video flex items-center justify-center text-zinc-500 text-sm";
            fallback.textContent = "Failed to load mockup image";
            parent.appendChild(fallback);
          }
        }}
      />
    </div>
  );
}

/* ---------- revision history sidebar ---------- */

function RevisionHistory({
  revisions,
  open,
  onOpenChange,
}: {
  revisions: RevisionEntry[];
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-[400px] bg-zinc-950 border-zinc-800 p-0 flex flex-col"
      >
        <div className="p-6 pb-4 border-b border-zinc-800">
          <SheetHeader>
            <SheetTitle className="text-white text-base">
              Revision History
            </SheetTitle>
            <SheetDescription className="text-zinc-400 text-xs">
              {revisions.length} revision{revisions.length !== 1 ? "s" : ""}{" "}
              recorded
            </SheetDescription>
          </SheetHeader>
        </div>

        <ScrollArea className="flex-1 p-6">
          {revisions.length === 0 ? (
            <div className="flex items-center justify-center h-32 text-zinc-500 text-sm">
              No revisions yet
            </div>
          ) : (
            <div className="space-y-4">
              {revisions.map((rev, idx) => (
                <div
                  key={rev.id ?? idx}
                  className="relative pl-6 pb-4 border-l-2 border-zinc-800 last:border-l-0"
                >
                  <div className="absolute -left-[5px] top-1 w-2 h-2 rounded-full bg-zinc-600" />
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <Badge
                        className={`text-[10px] capitalize ${
                          rev.action === "approve"
                            ? "bg-green-500/20 text-green-400"
                            : rev.action === "reject"
                            ? "bg-red-500/20 text-red-400"
                            : "bg-orange-500/20 text-orange-400"
                        }`}
                      >
                        {rev.action}
                      </Badge>
                      <span className="text-xs text-zinc-500">
                        {relativeTime(rev.timestamp)}
                      </span>
                    </div>
                    {rev.reviewer && (
                      <p className="text-xs text-zinc-400">
                        by {rev.reviewer}
                      </p>
                    )}
                    {rev.notes && (
                      <p className="text-sm text-zinc-300 mt-1">{rev.notes}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}

/* ---------- design review card ---------- */

function DesignReviewCard({
  item,
  onSelect,
  selected,
}: {
  item: HITLItem;
  onSelect: (item: HITLItem) => void;
  selected: boolean;
}) {
  const design = extractDesignPayload(item);

  return (
    <div
      onClick={() => onSelect(item)}
      className={`bg-zinc-900 border rounded-lg p-4 cursor-pointer transition-all hover:border-zinc-600 ${
        selected
          ? "border-orange-500 ring-1 ring-orange-500/30"
          : "border-zinc-800"
      }`}
    >
      {/* Thumbnail */}
      <div className="aspect-video bg-zinc-800 rounded-md mb-3 overflow-hidden flex items-center justify-center">
        {design.mockup_url ? (
          <img
            src={design.mockup_url}
            alt="Design thumbnail"
            className="w-full h-full object-cover"
            onError={(e) => {
              const target = e.target as HTMLImageElement;
              target.style.display = "none";
            }}
          />
        ) : (
          <svg
            className="h-8 w-8 text-zinc-600"
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
            <circle cx="8.5" cy="8.5" r="1.5" />
            <polyline points="21 15 16 10 5 21" />
          </svg>
        )}
      </div>

      {/* Info */}
      <p className="font-semibold text-white text-sm line-clamp-2 mb-1">
        {item.title}
      </p>
      <div className="flex items-center gap-2 mb-2">
        <Badge
          className={`text-[10px] border ${
            STATUS_COLORS[item.payload?.design_status as string] ??
            STATUS_COLORS.pending
          }`}
        >
          {(item.payload?.design_status as string) ?? "pending"}
        </Badge>
        {design.revision_number != null && (
          <span className="text-[10px] text-zinc-500">
            Rev {design.revision_number}
          </span>
        )}
      </div>
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-zinc-500">
          {design.designer ?? "Design Agent"}
        </span>
        <span className="text-[10px] text-zinc-500">
          {relativeTime(item.created_at)}
        </span>
      </div>
    </div>
  );
}

/* ---------- page ---------- */

export default function DesignReviewPage() {
  const queryClient = useQueryClient();
  const [selectedItem, setSelectedItem] = useState<HITLItem | null>(null);
  const [feedbackText, setFeedbackText] = useState("");
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  // WebSocket real-time updates
  useWsSubscription("subscribe:channel", "hitl:new");

  // Fetch design-related HITL items
  const { data, isLoading, error } = useQuery({
    queryKey: ["design-review-items"],
    queryFn: () =>
      fetchHITLPending({ type: "code_review", limit: 100 }),
    refetchInterval: 30_000,
  });

  const items = useMemo(() => {
    // Filter for design-type items (code_review with design payload, or any design-tagged items)
    const allItems = data?.items ?? [];
    return allItems;
  }, [data]);

  const selectedDesign = selectedItem
    ? extractDesignPayload(selectedItem)
    : null;
  const selectedRevisions = selectedItem
    ? extractRevisions(selectedItem)
    : [];

  const invalidateDesign = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["design-review-items"] });
    queryClient.invalidateQueries({ queryKey: ["hitl-pending"] });
    queryClient.invalidateQueries({ queryKey: ["hitl-pending-count"] });
  }, [queryClient]);

  const handleAction = useCallback(
    async (action: string) => {
      if (!selectedItem) return;
      setActionLoading(action);
      try {
        await resolveHITL(
          selectedItem.id,
          action,
          feedbackText || undefined
        );
        toast({
          title: `Design ${action}`,
          description: `Review action "${action}" applied successfully`,
          variant: action === "approve" ? "success" : "default",
        });
        setSelectedItem(null);
        setFeedbackText("");
        invalidateDesign();
      } catch (err) {
        toast({
          title: "Action failed",
          description:
            err instanceof Error ? err.message : "Unknown error",
          variant: "destructive",
        });
      } finally {
        setActionLoading(null);
      }
    },
    [selectedItem, feedbackText, invalidateDesign]
  );

  return (
    <div className="flex flex-col h-full space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-white">
            Design Review
          </h1>
          {items.length > 0 && (
            <Badge className="bg-orange-500/20 text-orange-400 text-xs border border-orange-500/30">
              {items.length} pending
            </Badge>
          )}
        </div>
        {selectedItem && (
          <div className="flex items-center gap-2">
            {selectedDesign?.preview_url && (
              <Button
                variant="outline"
                size="sm"
                className="border-zinc-700"
                onClick={() =>
                  window.open(selectedDesign.preview_url, "_blank")
                }
              >
                <svg
                  className="mr-2 h-3 w-3"
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
                  <polyline points="15 3 21 3 21 9" />
                  <line x1="10" y1="14" x2="21" y2="3" />
                </svg>
                Preview Link
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              className="border-zinc-700"
              onClick={() => setHistoryOpen(true)}
            >
              <svg
                className="mr-2 h-3 w-3"
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
              History ({selectedRevisions.length})
            </Button>
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load design reviews:{" "}
          {error instanceof Error ? error.message : "Unknown error"}
        </div>
      )}

      {/* Main content: split layout */}
      <div className="flex gap-4 flex-1 min-h-0">
        {/* Left: Card grid */}
        <div className="w-80 shrink-0 overflow-y-auto space-y-3">
          {isLoading ? (
            Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-48" />
            ))
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <div className="rounded-full bg-green-500/10 p-4 mb-4">
                <svg
                  className="h-8 w-8 text-green-500"
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
              <p className="text-white text-sm font-medium">All reviewed</p>
              <p className="text-zinc-500 text-xs mt-1">
                No pending design reviews
              </p>
            </div>
          ) : (
            items.map((item) => (
              <DesignReviewCard
                key={item.id}
                item={item}
                onSelect={setSelectedItem}
                selected={selectedItem?.id === item.id}
              />
            ))
          )}
        </div>

        {/* Right: Preview + Actions */}
        <div className="flex-1 flex flex-col min-h-0">
          {selectedItem ? (
            <>
              {/* Mockup preview */}
              <div className="flex-1 min-h-0 overflow-y-auto mb-4">
                <MockupPreview
                  url={selectedDesign?.mockup_url}
                  loading={false}
                />
                {/* Design notes */}
                {selectedDesign?.design_notes && (
                  <div className="mt-3 bg-zinc-900 border border-zinc-800 rounded-lg p-4">
                    <p className="text-xs font-medium text-zinc-400 mb-1">
                      Design Notes
                    </p>
                    <p className="text-sm text-zinc-200">
                      {selectedDesign.design_notes}
                    </p>
                  </div>
                )}

                {/* Meta info */}
                <div className="mt-3 flex items-center gap-4 text-xs text-zinc-500">
                  {selectedDesign?.pencil_project_id && (
                    <span>
                      Pencil ID: {selectedDesign.pencil_project_id}
                    </span>
                  )}
                  {selectedDesign?.revision_number != null && (
                    <span>Revision #{selectedDesign.revision_number}</span>
                  )}
                  {selectedDesign?.designer && (
                    <span>Designer: {selectedDesign.designer}</span>
                  )}
                </div>
              </div>

              {/* Feedback + Actions */}
              <div className="shrink-0 space-y-3 border-t border-zinc-800 pt-4">
                <Textarea
                  placeholder="Add feedback or change requests..."
                  value={feedbackText}
                  onChange={(e) => setFeedbackText(e.target.value)}
                  className="bg-zinc-900 border-zinc-700 text-white resize-none min-h-[80px]"
                />
                <div className="flex items-center gap-3">
                  <Button
                    className="bg-green-600 hover:bg-green-700 text-white flex-1"
                    disabled={actionLoading !== null}
                    onClick={() => handleAction("approve")}
                  >
                    {actionLoading === "approve" ? (
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
                        Approving...
                      </span>
                    ) : (
                      <>
                        <svg
                          className="mr-2 h-4 w-4"
                          xmlns="http://www.w3.org/2000/svg"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                        Approve
                      </>
                    )}
                  </Button>
                  <Button
                    variant="outline"
                    className="border-orange-500/50 text-orange-400 hover:bg-orange-500/10 flex-1"
                    disabled={actionLoading !== null}
                    onClick={() => handleAction("request_changes")}
                  >
                    {actionLoading === "request_changes" ? (
                      "Sending..."
                    ) : (
                      <>
                        <svg
                          className="mr-2 h-4 w-4"
                          xmlns="http://www.w3.org/2000/svg"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" />
                          <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" />
                        </svg>
                        Request Changes
                      </>
                    )}
                  </Button>
                  <Button
                    variant="destructive"
                    className="flex-1"
                    disabled={actionLoading !== null}
                    onClick={() => handleAction("reject")}
                  >
                    {actionLoading === "reject" ? (
                      "Rejecting..."
                    ) : (
                      <>
                        <svg
                          className="mr-2 h-4 w-4"
                          xmlns="http://www.w3.org/2000/svg"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <line x1="18" y1="6" x2="6" y2="18" />
                          <line x1="6" y1="6" x2="18" y2="18" />
                        </svg>
                        Reject
                      </>
                    )}
                  </Button>
                </div>
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center space-y-2">
                <svg
                  className="h-12 w-12 mx-auto text-zinc-700"
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                  <circle cx="8.5" cy="8.5" r="1.5" />
                  <polyline points="21 15 16 10 5 21" />
                </svg>
                <p className="text-zinc-500 text-sm">
                  Select a design to review
                </p>
                <p className="text-zinc-600 text-xs">
                  Click on a card from the left panel to view the mockup and
                  take action
                </p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Revision History Drawer */}
      <RevisionHistory
        revisions={selectedRevisions}
        open={historyOpen}
        onOpenChange={setHistoryOpen}
      />
    </div>
  );
}
