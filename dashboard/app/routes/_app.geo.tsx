import { useState } from "react";
import { useNavigate } from "@remix-run/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent } from "~/components/ui/card";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Pagination } from "~/components/pagination";
import { LazyMap } from "~/components/lazy-map";
import { fetchLeads, fetchPipelineBStats, startScan } from "~/lib/api";
import { toast } from "~/hooks/use-toast";
import type { Lead } from "~/lib/types";

const PAGE_SIZE = 50;

type ViewMode = "split" | "map" | "table";

const statusFilters = [
  { value: "all", label: "All" },
  { value: "new", label: "New" },
  { value: "enriched", label: "Enriched" },
  { value: "contacted", label: "Contacted" },
  { value: "failed", label: "Failed" },
] as const;

function statusBadgeVariant(
  status: string
): "success" | "secondary" | "default" | "destructive" | "warning" {
  switch (status) {
    case "enriched":
      return "success";
    case "contacted":
      return "default";
    case "failed":
      return "destructive";
    case "new":
      return "secondary";
    default:
      return "secondary";
  }
}

export default function GeoScoutPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("all");
  const [cityFilter, setCityFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [scanCity, setScanCity] = useState("");
  const [isScanning, setIsScanning] = useState(false);
  const [page, setPage] = useState(0);
  const [viewMode, setViewMode] = useState<ViewMode>("split");

  const handleStatusChange = (v: string) => { setStatusFilter(v); setPage(0); };

  const { data, isLoading, error } = useQuery({
    queryKey: ["geo-leads", statusFilter, cityFilter, categoryFilter, page],
    queryFn: () =>
      fetchLeads({
        status: statusFilter === "all" ? undefined : statusFilter,
        city: cityFilter || undefined,
        category: categoryFilter === "all" ? undefined : categoryFilter,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  const { data: stats } = useQuery({
    queryKey: ["pipeline-b-stats"],
    queryFn: fetchPipelineBStats,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  const handleScan = async () => {
    const city = scanCity.trim();
    if (!city) {
      toast({
        title: "City required",
        description: "Please enter a city name to scan.",
        variant: "destructive",
      });
      return;
    }

    setIsScanning(true);
    try {
      const result = await startScan(city);
      toast({
        title: "Scan started",
        description: result.message,
        variant: "success",
      });
      setScanCity("");
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ["geo-leads"] });
        queryClient.invalidateQueries({ queryKey: ["pipeline-b-stats"] });
      }, 3000);
    } catch (err) {
      toast({
        title: "Scan failed",
        description:
          err instanceof Error ? err.message : "Something went wrong",
        variant: "destructive",
      });
    } finally {
      setIsScanning(false);
    }
  };

  const leads = data?.leads ?? [];
  const total = data?.total ?? 0;

  // Extract unique categories from stats or leads
  const categories = Array.from(
    new Set(leads.map((l) => l.category).filter(Boolean) as string[])
  ).sort();

  const showMap = viewMode === "split" || viewMode === "map";
  const showTable = viewMode === "split" || viewMode === "table";

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Geo Scout</h1>
          {total > 0 && (
            <p className="text-sm text-muted-foreground mt-1">
              {total} leads found
            </p>
          )}
        </div>

        <div className="flex items-center gap-2">
          {stats && (
            <div className="hidden md:flex items-center gap-4 text-sm text-muted-foreground mr-4">
              <span>Total: {stats.total_leads}</span>
              {Object.entries(stats.by_status).map(([status, count]) => (
                <span key={status}>
                  {status}: {count}
                </span>
              ))}
            </div>
          )}

          {/* View mode toggle */}
          <div className="flex items-center rounded-md border border-border/50 p-0.5">
            <button
              onClick={() => setViewMode("split")}
              className={`px-2.5 py-1 text-xs rounded-sm transition-colors ${
                viewMode === "split"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              title="Split View"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <line x1="3" y1="12" x2="21" y2="12" />
              </svg>
            </button>
            <button
              onClick={() => setViewMode("map")}
              className={`px-2.5 py-1 text-xs rounded-sm transition-colors ${
                viewMode === "map"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              title="Map View"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                <path d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            </button>
            <button
              onClick={() => setViewMode("table")}
              className={`px-2.5 py-1 text-xs rounded-sm transition-colors ${
                viewMode === "table"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              title="Table View"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="8" y1="6" x2="21" y2="6" />
                <line x1="8" y1="12" x2="21" y2="12" />
                <line x1="8" y1="18" x2="21" y2="18" />
                <line x1="3" y1="6" x2="3.01" y2="6" />
                <line x1="3" y1="12" x2="3.01" y2="12" />
                <line x1="3" y1="18" x2="3.01" y2="18" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* Scan controls */}
      <Card className="border-border/50">
        <CardContent className="pt-6">
          <div className="flex items-end gap-3">
            <div className="flex-1 max-w-sm">
              <label
                htmlFor="geo-scan-city"
                className="text-sm font-medium text-foreground mb-1.5 block"
              >
                Start a Geo Scan
              </label>
              <Input
                id="geo-scan-city"
                placeholder="Enter city name (e.g. Berlin)"
                value={scanCity}
                onChange={(e) => setScanCity(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleScan();
                }}
              />
            </div>
            <Button
              onClick={handleScan}
              disabled={isScanning || !scanCity.trim()}
            >
              {isScanning ? (
                <span className="flex items-center gap-1.5">
                  <svg
                    className="h-4 w-4 animate-spin"
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
                  Scanning...
                </span>
              ) : (
                "Start Scan"
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <Tabs value={statusFilter} onValueChange={handleStatusChange}>
          <TabsList>
            {statusFilters.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        <Input
          placeholder="Filter by city..."
          value={cityFilter}
          onChange={(e) => { setCityFilter(e.target.value); setPage(0); }}
          className="max-w-[200px]"
        />

        {categories.length > 0 && (
          <Select value={categoryFilter} onValueChange={(v) => { setCategoryFilter(v); setPage(0); }}>
            <SelectTrigger className="w-[180px]">
              <SelectValue placeholder="Category" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Categories</SelectItem>
              {categories.map((cat) => (
                <SelectItem key={cat} value={cat}>
                  {cat}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        {/* Legend */}
        <div className="ml-auto hidden lg:flex items-center gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-blue-500" /> New</span>
          <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-green-500" /> Enriched</span>
          <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-orange-500" /> Contacted</span>
          <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-red-500" /> Failed</span>
        </div>
      </div>

      {/* Loading state */}
      {isLoading && !data && (
        <div className="space-y-4">
          <Skeleton className="h-[300px]" />
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-[100px]" />
            ))}
          </div>
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load leads:{" "}
          {error instanceof Error ? error.message : "Unknown error"}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !error && leads.length === 0 && (
        <Card className="border-border/50">
          <CardContent className="flex flex-col items-center justify-center py-16">
            <svg
              className="h-12 w-12 text-muted-foreground/30 mb-4"
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
              <path d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            <p className="text-muted-foreground text-lg font-medium">
              No leads found
            </p>
            <p className="text-muted-foreground/70 text-sm mt-1">
              Start a geo scan to discover local businesses
            </p>
          </CardContent>
        </Card>
      )}

      {/* Map + Table content */}
      {leads.length > 0 && (
        <div className={viewMode === "split" ? "space-y-4" : ""}>
          {/* Map */}
          {showMap && (
            <LazyMap
              leads={leads}
              onMarkerClick={(id) => navigate(`/leads/${id}`)}
              className={viewMode === "split" ? "h-[400px]" : "h-[600px]"}
            />
          )}

          {/* Table */}
          {showTable && (
            <Card className="border-border/50">
              <CardContent className="p-0">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border/50">
                        <th className="text-left py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Name</th>
                        <th className="text-left py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">City</th>
                        <th className="text-left py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Category</th>
                        <th className="text-left py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Contact</th>
                        <th className="text-left py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {leads.map((lead) => (
                        <tr
                          key={lead.id}
                          className="border-b border-border/30 hover:bg-accent/30 transition-colors cursor-pointer"
                          onClick={() => navigate(`/leads/${lead.id}`)}
                        >
                          <td className="py-2.5 px-4">
                            <span className="font-medium text-foreground">{lead.name}</span>
                          </td>
                          <td className="py-2.5 px-4 text-muted-foreground">{lead.city ?? "--"}</td>
                          <td className="py-2.5 px-4 text-muted-foreground">{lead.category ?? "--"}</td>
                          <td className="py-2.5 px-4">
                            <div className="text-xs text-muted-foreground space-y-0.5">
                              {lead.email && <p>{lead.email}</p>}
                              {lead.phone && <p>{lead.phone}</p>}
                              {!lead.email && !lead.phone && <p>--</p>}
                            </div>
                          </td>
                          <td className="py-2.5 px-4">
                            <Badge variant={statusBadgeVariant(lead.status)}>{lead.status}</Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Pagination */}
      <Pagination
        page={page}
        pageSize={PAGE_SIZE}
        total={total}
        onPageChange={setPage}
      />
    </div>
  );
}
