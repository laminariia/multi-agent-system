import { useState, useCallback, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import {
  DndContext,
  closestCenter,
  DragOverlay,
  type DragStartEvent,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  verticalListSortingStrategy,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Textarea } from "~/components/ui/textarea";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "~/components/ui/sheet";
import { fetchBids, fetchBidStats, updateBidStatus } from "~/lib/api";
import type { Bid, BidStats, BidMessage, NegotiationStatus } from "~/lib/types";
import { relativeTime } from "~/lib/utils";
import { useWsSubscription } from "~/hooks/use-ws-subscription";
import { toast } from "~/hooks/use-toast";

/* ---------- helpers ---------- */

function formatCurrency(v: number | null): string {
  if (v == null) return "--";
  return `$${v.toLocaleString()}`;
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + "..." : s;
}

/* ---------- column config ---------- */

const STATUS_TO_COLUMN: Record<string, string> = {
  draft: "backlog",
  submitted: "sent",
  active_dialog: "active",
  accepted: "confirmed",
  rejected: "rejected",
  withdrawn: "rejected",
};

const COLUMN_TO_STATUS: Record<string, string> = {
  backlog: "draft",
  sent: "submitted",
  active: "active_dialog",
  confirmed: "accepted",
  rejected: "rejected",
};

const COLUMN_COLORS: Record<string, string> = {
  backlog: "bg-zinc-500",
  sent: "bg-blue-500",
  active: "bg-orange-500",
  confirmed: "bg-green-500",
  rejected: "bg-red-500",
};

const COLUMNS: {
  key: string;
  label: string;
  statuses: string[];
}[] = [
  { key: "backlog", label: "Backlog", statuses: ["draft"] },
  { key: "sent", label: "Sent", statuses: ["submitted"] },
  { key: "active", label: "Active", statuses: ["active_dialog"] },
  { key: "confirmed", label: "Confirmed", statuses: ["accepted"] },
  { key: "rejected", label: "Rejected", statuses: ["rejected", "withdrawn"] },
];

/* ---------- negotiation badge ---------- */

const NEGOTIATION_COLORS: Record<NegotiationStatus, string> = {
  initial: "bg-zinc-600 text-zinc-200",
  qualifying: "bg-blue-500/20 text-blue-400",
  proposing: "bg-purple-500/20 text-purple-400",
  negotiating: "bg-orange-500/20 text-orange-400",
  closing: "bg-green-500/20 text-green-400",
};

function NegotiationBadge({ status }: { status: NegotiationStatus }) {
  return (
    <Badge className={`${NEGOTIATION_COLORS[status]} text-[10px] capitalize`}>
      {status}
    </Badge>
  );
}

/* ---------- sender colors ---------- */

const SENDER_COLORS: Record<BidMessage["sender"], string> = {
  ai: "text-blue-400",
  client: "text-green-400",
  operator: "text-orange-400",
};

const SENDER_BG: Record<BidMessage["sender"], string> = {
  ai: "bg-blue-500/10",
  client: "bg-green-500/10",
  operator: "bg-orange-500/10",
};

/* ---------- sortable bid card ---------- */

function SortableBidCard({
  bid,
  onClick,
}: {
  bid: Bid;
  onClick: (bid: Bid) => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: bid.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      onClick={() => onClick(bid)}
      className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 space-y-3 cursor-grab active:cursor-grabbing hover:border-zinc-600 transition-colors"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="font-semibold text-white text-sm leading-snug line-clamp-2">
          {bid.job_title ?? `Bid #${bid.id.slice(0, 8)}`}
        </p>
        {bid.status === "submitted" && (
          <Badge className="bg-orange-500/20 text-orange-400 text-[10px] shrink-0">
            Sent
          </Badge>
        )}
        {bid.status === "accepted" && (
          <Badge className="bg-green-500/20 text-green-400 text-[10px] shrink-0">
            Won
          </Badge>
        )}
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] bg-zinc-800 text-zinc-400 px-2 py-0.5 rounded">
          {bid.platform}
        </span>
        {bid.delivery_days != null && (
          <span className="text-[10px] text-zinc-500">
            {bid.delivery_days}d delivery
          </span>
        )}
        {(bid as unknown as Record<string, unknown>).score != null && (
          <span className="text-[10px] bg-orange-500/20 text-orange-400 px-2 py-0.5 rounded font-medium">
            {Math.round(Number((bid as unknown as Record<string, unknown>).score) * 100)}%
          </span>
        )}
      </div>

      {bid.cover_letter && (
        <p className="text-xs text-zinc-500 line-clamp-1">
          {truncate(bid.cover_letter, 60)}
        </p>
      )}

      <div className="flex items-center justify-between pt-1">
        <span className="text-orange-400 font-medium text-xs">
          {formatCurrency(bid.bid_amount)}
        </span>
        <span className="text-zinc-500 text-[11px]">
          {relativeTime(bid.submitted_at ?? bid.created_at)}
        </span>
      </div>
    </div>
  );
}

/* ---------- static bid card (for DragOverlay) ---------- */

function BidCardStatic({ bid }: { bid: Bid }) {
  return (
    <div className="bg-zinc-900 border border-orange-500 rounded-lg p-4 space-y-3 shadow-xl shadow-orange-500/10">
      <div className="flex items-start justify-between gap-2">
        <p className="font-semibold text-white text-sm leading-snug line-clamp-2">
          {bid.job_title ?? `Bid #${bid.id.slice(0, 8)}`}
        </p>
      </div>
      <div className="flex items-center justify-between pt-1">
        <span className="text-orange-400 font-medium text-xs">
          {formatCurrency(bid.bid_amount)}
        </span>
        <span className="text-zinc-500 text-[11px]">
          {bid.platform}
        </span>
      </div>
    </div>
  );
}

/* ---------- kanban column ---------- */

function KanbanColumn({
  label,
  columnKey,
  bids,
  loading,
  onCardClick,
}: {
  label: string;
  columnKey: string;
  bids: Bid[];
  loading: boolean;
  onCardClick: (bid: Bid) => void;
}) {
  const bidIds = useMemo(() => bids.map((b) => b.id), [bids]);

  return (
    <div className="flex flex-col min-h-0" data-column={columnKey}>
      <div className={`h-1 ${COLUMN_COLORS[columnKey] ?? "bg-orange-500"} rounded-t-md`} />
      <div className="bg-zinc-900/50 border border-zinc-800 border-t-0 rounded-b-md p-3 flex items-center justify-between mb-3">
        <span className="text-sm font-semibold text-white">{label}</span>
        <Badge className="bg-zinc-800 text-zinc-300 text-[10px]">
          {bids.length}
        </Badge>
      </div>
      <SortableContext items={bidIds} strategy={verticalListSortingStrategy}>
        <div className="flex flex-col gap-3 overflow-y-auto flex-1 min-h-[80px]">
          {loading
            ? Array.from({ length: 2 }).map((_, i) => (
                <Skeleton key={i} className="h-32" />
              ))
            : bids.map((bid) => (
                <SortableBidCard
                  key={bid.id}
                  bid={bid}
                  onClick={onCardClick}
                />
              ))}
          {!loading && bids.length === 0 && (
            <div className="text-center text-zinc-600 text-xs py-8">Empty</div>
          )}
        </div>
      </SortableContext>
    </div>
  );
}

/* ---------- chat drawer ---------- */

function ChatDrawer({
  bid,
  open,
  onOpenChange,
}: {
  bid: Bid | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [messageText, setMessageText] = useState("");
  const [sending, setSending] = useState(false);

  // Mock data -- chat API may not exist yet
  const mockMessages: BidMessage[] = useMemo(() => [], []);
  const mockNegotiationStatus: NegotiationStatus = "initial";

  const handleSend = useCallback(() => {
    if (!messageText.trim() || !bid) return;
    setSending(true);
    // Placeholder: when chat API is ready, POST message here
    setTimeout(() => {
      setSending(false);
      setMessageText("");
      toast({
        title: "Message queued",
        description: "Chat API integration pending",
      });
    }, 300);
  }, [messageText, bid]);

  if (!bid) return null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-[600px] bg-zinc-950 border-zinc-800 p-0 flex flex-col"
      >
        {/* Header */}
        <div className="p-6 pb-4 border-b border-zinc-800 space-y-3">
          <SheetHeader>
            <SheetTitle className="text-white text-base">
              {bid.job_title ?? `Bid #${bid.id.slice(0, 8)}`}
            </SheetTitle>
            <SheetDescription className="text-zinc-400 text-xs">
              {bid.platform} -- {formatCurrency(bid.bid_amount)}
              {bid.delivery_days != null && ` -- ${bid.delivery_days}d delivery`}
            </SheetDescription>
          </SheetHeader>
          <div className="flex items-center gap-2">
            <NegotiationBadge status={mockNegotiationStatus} />
            <Badge className="bg-zinc-800 text-zinc-300 text-[10px] capitalize">
              {bid.status}
            </Badge>
          </div>
        </div>

        {/* Messages area */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {mockMessages.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <div className="text-center space-y-2">
                <p className="text-zinc-500 text-sm">No messages yet</p>
                <p className="text-zinc-600 text-xs">
                  Messages will appear here when the negotiation begins
                </p>
              </div>
            </div>
          ) : (
            mockMessages.map((msg) => (
              <div key={msg.id} className="space-y-1">
                <div className="flex items-center gap-2">
                  <span
                    className={`text-xs font-medium capitalize ${
                      SENDER_COLORS[msg.sender]
                    }`}
                  >
                    {msg.sender}
                  </span>
                  <span className="text-zinc-600 text-[10px]">
                    {relativeTime(msg.timestamp)}
                  </span>
                </div>
                <div
                  className={`rounded-lg px-3 py-2 text-sm text-zinc-200 ${
                    SENDER_BG[msg.sender]
                  }`}
                >
                  {msg.text}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Input area */}
        <div className="p-4 border-t border-zinc-800 space-y-3">
          <div className="flex gap-2">
            <Textarea
              placeholder="Type a message..."
              value={messageText}
              onChange={(e) => setMessageText(e.target.value)}
              className="bg-zinc-900 border-zinc-700 text-white resize-none min-h-[60px]"
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
            />
          </div>
          <div className="flex items-center justify-between">
            <Button
              variant="outline"
              size="sm"
              className="border-orange-500/50 text-orange-400 hover:bg-orange-500/10"
            >
              Take Over
            </Button>
            <Button
              size="sm"
              className="bg-orange-500 hover:bg-orange-600 text-white"
              disabled={!messageText.trim() || sending}
              onClick={handleSend}
            >
              {sending ? "Sending..." : "Send"}
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

/* ---------- stats header ---------- */

function StatsHeader({ stats }: { stats: BidStats | undefined }) {
  return (
    <div className="grid grid-cols-3 gap-4 shrink-0">
      <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg p-4">
        <p className="text-zinc-500 text-xs mb-1">Total Bids</p>
        <p className="text-2xl font-bold text-white">
          {stats?.total ?? "--"}
        </p>
      </div>
      <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg p-4">
        <p className="text-zinc-500 text-xs mb-1">Win Rate</p>
        <p className="text-2xl font-bold text-orange-400">
          {stats?.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : "--"}
        </p>
      </div>
      <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg p-4">
        <p className="text-zinc-500 text-xs mb-1">Avg Bid Amount</p>
        <p className="text-2xl font-bold text-white">
          {stats?.avg_bid_amount != null
            ? formatCurrency(stats.avg_bid_amount)
            : "--"}
        </p>
      </div>
    </div>
  );
}

/* ---------- page ---------- */

export default function BidsPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [activeDragBid, setActiveDragBid] = useState<Bid | null>(null);
  const [selectedBid, setSelectedBid] = useState<Bid | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  // WebSocket subscription for live bid updates
  useWsSubscription("subscribe:channel", "bid:update");

  const { data, isLoading, error } = useQuery({
    queryKey: ["bids"],
    queryFn: () => fetchBids({ limit: 200 }),
    refetchInterval: 30_000,
  });

  const { data: stats } = useQuery({
    queryKey: ["bid-stats"],
    queryFn: fetchBidStats,
    refetchInterval: 60_000,
  });

  const allBids = data?.bids ?? [];

  const filtered = useMemo(() => {
    if (!search) return allBids;
    const q = search.toLowerCase();
    return allBids.filter(
      (b) =>
        (b.job_title ?? "").toLowerCase().includes(q) ||
        b.platform.toLowerCase().includes(q)
    );
  }, [allBids, search]);

  const columns = useMemo(
    () =>
      COLUMNS.map((col) => ({
        ...col,
        bids: filtered.filter((b) => col.statuses.includes(b.status)),
      })),
    [filtered]
  );

  const newCount = allBids.filter((b) => b.status === "draft").length;

  /* --- dnd handlers --- */

  const findColumnForBid = useCallback(
    (bidId: string): string | null => {
      const bid = allBids.find((b) => b.id === bidId);
      if (!bid) return null;
      return STATUS_TO_COLUMN[bid.status] ?? null;
    },
    [allBids]
  );

  const handleDragStart = useCallback(
    (event: DragStartEvent) => {
      const bid = allBids.find((b) => b.id === event.active.id);
      setActiveDragBid(bid ?? null);
    },
    [allBids]
  );

  const handleDragEnd = useCallback(
    async (event: DragEndEvent) => {
      setActiveDragBid(null);
      const { active, over } = event;
      if (!over) return;

      const bidId = String(active.id);
      const bid = allBids.find((b) => b.id === bidId);
      if (!bid) return;

      // Determine which column the bid was dropped into.
      // The "over" target can be another bid card (same or different column)
      // or a droppable column container.
      let targetColumn: string | null = null;

      // Check if dropped over another bid -- find that bid's column
      const overBid = allBids.find((b) => b.id === String(over.id));
      if (overBid) {
        targetColumn = STATUS_TO_COLUMN[overBid.status] ?? null;
      }

      // If over target is a column key directly
      if (!targetColumn && COLUMN_TO_STATUS[String(over.id)]) {
        targetColumn = String(over.id);
      }

      if (!targetColumn) return;

      const sourceColumn = findColumnForBid(bidId);
      if (sourceColumn === targetColumn) return;

      const newStatus = COLUMN_TO_STATUS[targetColumn];
      if (!newStatus) return;

      // Optimistic update
      queryClient.setQueryData(
        ["bids"],
        (old: { bids: Bid[]; total: number } | undefined) => {
          if (!old) return old;
          return {
            ...old,
            bids: old.bids.map((b) =>
              b.id === bidId ? { ...b, status: newStatus as Bid["status"] } : b
            ),
          };
        }
      );

      try {
        await updateBidStatus(bidId, newStatus);
        queryClient.invalidateQueries({ queryKey: ["bids"] });
        queryClient.invalidateQueries({ queryKey: ["bid-stats"] });
        toast({
          title: "Bid moved",
          description: `Status changed to ${newStatus}`,
        });
      } catch (err) {
        // Revert on failure
        queryClient.invalidateQueries({ queryKey: ["bids"] });
        toast({
          title: "Failed to move bid",
          description:
            err instanceof Error ? err.message : "Unknown error",
          variant: "destructive",
        });
      }
    },
    [allBids, findColumnForBid, queryClient]
  );

  const handleCardClick = useCallback((bid: Bid) => {
    setSelectedBid(bid);
    setDrawerOpen(true);
  }, []);

  return (
    <div className="flex flex-col h-full space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-4 shrink-0">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-white">
            Bid Kanban
          </h1>
          {newCount > 0 && (
            <Badge className="bg-orange-500 text-white text-xs">
              {newCount} new
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Input
            placeholder="Search bids..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-56 bg-zinc-900 border-zinc-700"
          />
          <Button variant="outline" size="sm" className="border-zinc-700">
            Filter
          </Button>
          <Button
            size="sm"
            className="bg-orange-500 hover:bg-orange-600 text-white"
          >
            New Bid
          </Button>
        </div>
      </div>

      {/* Stats */}
      <StatsHeader stats={stats} />

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load bids:{" "}
          {error instanceof Error ? error.message : "Unknown error"}
        </div>
      )}

      {/* Kanban grid */}
      <DndContext
        collisionDetection={closestCenter}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        <div className="grid grid-cols-5 gap-4 flex-1 min-h-0">
          {columns.map((col) => (
            <KanbanColumn
              key={col.key}
              columnKey={col.key}
              label={col.label}
              bids={col.bids}
              loading={isLoading}
              onCardClick={handleCardClick}
            />
          ))}
        </div>
        <DragOverlay>
          {activeDragBid ? <BidCardStatic bid={activeDragBid} /> : null}
        </DragOverlay>
      </DndContext>

      {/* Chat Drawer */}
      <ChatDrawer
        bid={selectedBid}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
      />
    </div>
  );
}
