import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { fetchBids } from "~/lib/api";
import type { Bid } from "~/lib/types";

function formatCurrency(v: number | null): string {
  if (v == null) return "--";
  return `$${v.toLocaleString()}`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

const COLUMNS: { key: string; label: string; statuses: Bid["status"][] }[] = [
  { key: "drafting", label: "Drafting", statuses: ["draft"] },
  { key: "in_review", label: "In Review", statuses: ["submitted"] },
  { key: "approved", label: "Approved / Won", statuses: ["accepted"] },
  { key: "done", label: "Rejected / Done", statuses: ["rejected", "withdrawn"] },
];

function BidCard({ bid }: { bid: Bid }) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 space-y-3">
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

      <div className="flex items-center gap-2">
        <span className="text-[10px] bg-zinc-800 text-zinc-400 px-2 py-0.5 rounded">
          {bid.platform}
        </span>
        {bid.delivery_days != null && (
          <span className="text-[10px] text-zinc-500">
            {bid.delivery_days}d delivery
          </span>
        )}
      </div>

      {bid.cover_letter && (
        <p className="text-xs text-zinc-500 line-clamp-2">{bid.cover_letter}</p>
      )}

      <div className="flex items-center justify-between pt-1">
        <span className="text-orange-400 font-medium text-xs">
          {formatCurrency(bid.bid_amount)}
        </span>
        <span className="text-zinc-500 text-[11px]">
          {formatDate(bid.submitted_at ?? bid.created_at)}
        </span>
      </div>
    </div>
  );
}

function KanbanColumn({
  label,
  bids,
  loading,
}: {
  label: string;
  bids: Bid[];
  loading: boolean;
}) {
  return (
    <div className="flex flex-col min-h-0">
      <div className="h-1 bg-orange-500 rounded-t-md" />
      <div className="bg-zinc-900/50 border border-zinc-800 border-t-0 rounded-b-md p-3 flex items-center justify-between mb-3">
        <span className="text-sm font-semibold text-white">{label}</span>
        <Badge className="bg-zinc-800 text-zinc-300 text-[10px]">
          {bids.length}
        </Badge>
      </div>
      <div className="flex flex-col gap-3 overflow-y-auto flex-1">
        {loading
          ? Array.from({ length: 2 }).map((_, i) => (
              <Skeleton key={i} className="h-32" />
            ))
          : bids.map((bid) => <BidCard key={bid.id} bid={bid} />)}
        {!loading && bids.length === 0 && (
          <div className="text-center text-zinc-600 text-xs py-8">Empty</div>
        )}
      </div>
    </div>
  );
}

export default function BidsPage() {
  const [search, setSearch] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["bids"],
    queryFn: () => fetchBids({ limit: 100 }),
    refetchInterval: 30_000,
  });

  const allBids = data?.bids ?? [];

  const filtered = search
    ? allBids.filter((b) =>
        (b.job_title ?? "").toLowerCase().includes(search.toLowerCase()) ||
        b.platform.toLowerCase().includes(search.toLowerCase())
      )
    : allBids;

  const columns = COLUMNS.map((col) => ({
    ...col,
    bids: filtered.filter((b) =>
      (col.statuses as string[]).includes(b.status)
    ),
  }));

  const newCount = allBids.filter((b) => b.status === "draft").length;

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

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load bids:{" "}
          {error instanceof Error ? error.message : "Unknown error"}
        </div>
      )}

      {/* Kanban grid */}
      <div className="grid grid-cols-4 gap-4 flex-1 min-h-0">
        {columns.map((col) => (
          <KanbanColumn
            key={col.key}
            label={col.label}
            bids={col.bids}
            loading={isLoading}
          />
        ))}
      </div>
    </div>
  );
}
