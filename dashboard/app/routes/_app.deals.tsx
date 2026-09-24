import { useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Link } from "@remix-run/react";
import { fetchDeals } from "~/lib/api";
import type { Deal } from "~/lib/types";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Input } from "~/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";

const PAGE_SIZE = 20;

const STATUS_COLORS: Record<string, string> = {
  negotiation: "bg-yellow-100 text-yellow-800",
  won: "bg-green-100 text-green-800",
  lost: "bg-red-100 text-red-800",
  in_development: "bg-blue-100 text-blue-800",
  delivered: "bg-purple-100 text-purple-800",
};

function statusBadge(status: string) {
  const cls = STATUS_COLORS[status] ?? "bg-gray-100 text-gray-800";
  return <Badge className={cls}>{status.replace("_", " ")}</Badge>;
}

function formatCurrency(amount: number | null): string {
  if (amount == null) return "--";
  return `$${amount.toLocaleString()}`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "--";
  return new Date(iso).toLocaleDateString();
}

export default function DealsPage() {
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("newest");
  const [page, setPage] = useState(0);

  const { data, isLoading, error } = useQuery({
    queryKey: ["deals", statusFilter, search, sort, page],
    queryFn: () =>
      fetchDeals({
        status: statusFilter !== "all" ? statusFilter : undefined,
        search: search || undefined,
        sort,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
  });

  const deals = data?.deals ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Deals</h1>
          <p className="text-muted-foreground">
            Pipeline B → A bridge. Manage deals from leads to development.
          </p>
        </div>
        <Badge variant="outline" className="text-sm">
          {total} total
        </Badge>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <Input
          placeholder="Search by title..."
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
          className="w-64"
        />
        <Select
          value={statusFilter}
          onValueChange={(v) => {
            setStatusFilter(v);
            setPage(0);
          }}
        >
          <SelectTrigger className="w-44">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="negotiation">Negotiation</SelectItem>
            <SelectItem value="won">Won</SelectItem>
            <SelectItem value="lost">Lost</SelectItem>
            <SelectItem value="in_development">In Development</SelectItem>
            <SelectItem value="delivered">Delivered</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sort} onValueChange={setSort}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="Sort" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="newest">Newest first</SelectItem>
            <SelectItem value="oldest">Oldest first</SelectItem>
            <SelectItem value="title_asc">Title A-Z</SelectItem>
            <SelectItem value="budget_desc">Budget high-low</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        </div>
      ) : error ? (
        <Card>
          <CardContent className="py-8 text-center text-destructive">
            Failed to load deals: {(error as Error).message}
          </CardContent>
        </Card>
      ) : deals.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            No deals found. Deals are created from Pipeline B leads.
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {deals.map((deal: Deal) => (
              <Link key={deal.id} to={`/deals/${deal.id}`} className="block">
                <Card className="hover:border-primary/50 transition-colors cursor-pointer h-full">
                  <CardHeader className="pb-2">
                    <div className="flex items-start justify-between gap-2">
                      <CardTitle className="text-base line-clamp-2">
                        {deal.title}
                      </CardTitle>
                      {statusBadge(deal.status)}
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm text-muted-foreground">
                    <div className="flex justify-between">
                      <span>Budget</span>
                      <span className="font-medium text-foreground">
                        {formatCurrency(deal.budget)}
                      </span>
                    </div>
                    {deal.deadline && (
                      <div className="flex justify-between">
                        <span>Deadline</span>
                        <span>{formatDate(deal.deadline)}</span>
                      </div>
                    )}
                    {deal.agreed_scope && (
                      <p className="line-clamp-2 pt-1 text-xs">
                        {deal.agreed_scope}
                      </p>
                    )}
                    <div className="flex justify-between pt-1 text-xs">
                      <span>Created {formatDate(deal.created_at)}</span>
                      {deal.pipeline_a_thread_id && (
                        <Badge variant="outline" className="text-[10px]">
                          Pipeline A linked
                        </Badge>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                Showing {page * PAGE_SIZE + 1}–
                {Math.min((page + 1) * PAGE_SIZE, total)} of {total}
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page === 0}
                  onClick={() => setPage((p) => p - 1)}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages - 1}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
