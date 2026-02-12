import { useState } from "react";
import { useNavigate } from "@remix-run/react";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent } from "~/components/ui/card";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Pagination } from "~/components/pagination";
import { JobsByPlatformChart } from "~/components/charts";
import { MetricCard } from "~/components/metric-card";
import { fetchLeads, fetchPipelineBStats, startScan, enrichLead } from "~/lib/api";
import { downloadCSV } from "~/lib/utils";
import { toast } from "~/hooks/use-toast";
import type { Lead } from "~/lib/types";

const PAGE_SIZE = 24;

const statusFilters = [
  { value: "all", label: "All" },
  { value: "new", label: "New" },
  { value: "enriched", label: "Enriched" },
  { value: "contacted", label: "Contacted" },
  { value: "failed", label: "Failed" },
] as const;

const enrichmentSourceFilters = [
  { value: "all", label: "All Sources" },
  { value: "osint", label: "OSINT" },
  { value: "hunter", label: "Hunter" },
  { value: "apollo", label: "Apollo" },
] as const;

const sortOptions = [
  { value: "newest", label: "Newest" },
  { value: "oldest", label: "Oldest" },
  { value: "name_asc", label: "Name A-Z" },
  { value: "name_desc", label: "Name Z-A" },
  { value: "city_asc", label: "City A-Z" },
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

function EnrichmentIcon({ source }: { source: string | null }) {
  if (!source) {
    return (
      <span title="Not enriched" className="inline-flex items-center justify-center h-5 w-5 rounded-full bg-muted text-muted-foreground">
        <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="8" x2="12" y2="12" />
          <line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
      </span>
    );
  }

  const colors: Record<string, string> = {
    osint: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
    hunter: "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400",
    apollo: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
  };

  return (
    <span
      title={`Enriched via ${source}`}
      className={`inline-flex items-center justify-center h-5 w-5 rounded-full text-[9px] font-bold uppercase ${colors[source.toLowerCase()] ?? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"}`}
    >
      {source[0]}
    </span>
  );
}

export default function LeadsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("all");
  const [cityFilter, setCityFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [scanCity, setScanCity] = useState("");
  const [sortOrder, setSortOrder] = useState("newest");
  const [isScanning, setIsScanning] = useState(false);
  const [page, setPage] = useState(0);

  const handleStatusChange = (v: string) => { setStatusFilter(v); setPage(0); };

  const { data, isLoading, error } = useQuery({
    queryKey: ["leads", statusFilter, cityFilter, categoryFilter, sortOrder, page],
    queryFn: () =>
      fetchLeads({
        status: statusFilter === "all" ? undefined : statusFilter,
        city: cityFilter || undefined,
        category: categoryFilter === "all" ? undefined : categoryFilter,
        sort: sortOrder === "newest" ? undefined : sortOrder,
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
        queryClient.invalidateQueries({ queryKey: ["leads"] });
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

  // Filter by enrichment source client-side (API may not support it)
  const filteredLeads = sourceFilter === "all"
    ? leads
    : leads.filter((l) => l.enrichment_source?.toLowerCase() === sourceFilter);

  // Extract unique categories
  const categories = Array.from(
    new Set(leads.map((l) => l.category).filter(Boolean) as string[])
  ).sort();

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Leads</h1>
          {total > 0 && (
            <p className="text-sm text-muted-foreground mt-1">
              {total} leads found
            </p>
          )}
        </div>

        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 w-full sm:w-auto">
          {stats && (
            <div className="flex items-center gap-4 text-sm text-muted-foreground">
              <span>Total: {stats.total_leads}</span>
              {Object.entries(stats.by_status).map(([status, count]) => (
                <span key={status}>
                  {status}: {count}
                </span>
              ))}
            </div>
          )}
          <Button
            variant="outline"
            size="sm"
            disabled={leads.length === 0}
            onClick={() => {
              const rows = leads.map((l) => ({
                name: l.name,
                category: l.category ?? "",
                city: l.city ?? "",
                phone: l.phone ?? "",
                email: l.email ?? "",
                status: l.status,
                enrichment_source: l.enrichment_source ?? "",
                discovered_at: l.discovered_at ?? "",
              }));
              downloadCSV(
                rows,
                `leads-${new Date().toISOString().slice(0, 10)}.csv`
              );
            }}
          >
            Export CSV
          </Button>
        </div>
      </div>

      {/* Scan controls */}
      <Card className="border-border/50">
        <CardContent className="pt-6">
          <div className="flex items-end gap-3">
            <div className="flex-1 max-w-sm">
              <label
                htmlFor="scan-city"
                className="text-sm font-medium text-foreground mb-1.5 block"
              >
                Start a Geo Scan
              </label>
              <Input
                id="scan-city"
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

      {/* Stats summary */}
      {stats && (
        <div className="grid gap-4 md:grid-cols-3">
          <MetricCard
            icon={
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                <path d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            }
            label="Total Leads"
            value={stats.total_leads}
          />
          <MetricCard
            icon={
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
              </svg>
            }
            label="Enriched"
            value={stats.by_status["enriched"] ?? 0}
          />
          <MetricCard
            icon={
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 2L11 13" />
                <path d="M22 2l-7 20-4-9-9-4 20-7z" />
              </svg>
            }
            label="Contacted"
            value={stats.by_status["contacted"] ?? 0}
          />
        </div>
      )}

      {/* Top cities chart */}
      {stats && stats.top_cities.length > 0 && (
        <JobsByPlatformChart
          title="Leads by City"
          data={stats.top_cities.map((c) => ({
            platform: c.city ?? "Unknown",
            count: c.count,
          }))}
        />
      )}

      {/* Filters */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 sm:gap-3 flex-wrap">
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
          className="w-full sm:w-[200px]"
        />

        {categories.length > 0 && (
          <Select value={categoryFilter} onValueChange={(v) => { setCategoryFilter(v); setPage(0); }}>
            <SelectTrigger className="w-full sm:w-[160px]">
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

        <Select value={sourceFilter} onValueChange={(v) => { setSourceFilter(v); setPage(0); }}>
          <SelectTrigger className="w-full sm:w-[160px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {enrichmentSourceFilters.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={sortOrder} onValueChange={(v) => { setSortOrder(v); setPage(0); }}>
          <SelectTrigger className="w-full sm:w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {sortOptions.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Loading state */}
      {isLoading && !data && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-[160px]" />
          ))}
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
      {!isLoading && !error && filteredLeads.length === 0 && (
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

      {/* Leads grid */}
      {filteredLeads.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {filteredLeads.map((lead) => (
            <LeadCard key={lead.id} lead={lead} onClick={() => navigate(`/leads/${lead.id}`)} />
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
    </div>
  );
}

function LeadCard({ lead, onClick }: { lead: Lead; onClick?: () => void }) {
  const queryClient = useQueryClient();
  const [enriching, setEnriching] = useState(false);

  const handleEnrich = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setEnriching(true);
    try {
      await enrichLead(lead.id);
      toast({ title: "Enrichment started", description: `Enriching ${lead.name}`, variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["leads"] });
      queryClient.invalidateQueries({ queryKey: ["pipeline-b-stats"] });
    } catch (err) {
      toast({
        title: "Enrichment failed",
        description: err instanceof Error ? err.message : "Something went wrong",
        variant: "destructive",
      });
    } finally {
      setEnriching(false);
    }
  };

  return (
    <Card className="border-border/50 hover:border-primary/30 transition-colors animate-fade-in cursor-pointer" onClick={onClick}>
      <CardContent className="pt-6">
        <div className="flex items-start justify-between gap-2 mb-3">
          <div className="flex items-center gap-2 min-w-0">
            <EnrichmentIcon source={lead.enrichment_source} />
            <h3 className="text-sm font-semibold text-foreground leading-tight truncate">
              {lead.name}
            </h3>
          </div>
          <Badge variant={statusBadgeVariant(lead.status)}>
            {lead.status}
          </Badge>
        </div>

        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          {lead.city && (
            <div>
              <span className="text-muted-foreground">City: </span>
              <span className="text-foreground/90">{lead.city}</span>
            </div>
          )}
          {lead.category && (
            <div>
              <span className="text-muted-foreground">Category: </span>
              <span className="text-foreground/90">{lead.category}</span>
            </div>
          )}
          {lead.phone && (
            <div>
              <span className="text-muted-foreground">Phone: </span>
              <span className="text-foreground/90">{lead.phone}</span>
            </div>
          )}
          {lead.email && (
            <div>
              <span className="text-muted-foreground">Email: </span>
              <span className="text-foreground/90">{lead.email}</span>
            </div>
          )}
          {lead.address && (
            <div className="col-span-2">
              <span className="text-muted-foreground">Address: </span>
              <span className="text-foreground/90">{lead.address}</span>
            </div>
          )}
          {lead.enrichment_source && (
            <div>
              <span className="text-muted-foreground">Source: </span>
              <span className="text-foreground/90">
                {lead.enrichment_source}
              </span>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between mt-3">
          {lead.discovered_at && (
            <p className="text-xs text-muted-foreground/70">
              Discovered{" "}
              {new Date(lead.discovered_at).toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </p>
          )}
          {lead.status === "new" && (
            <Button
              variant="outline"
              size="sm"
              className="text-xs h-7"
              onClick={handleEnrich}
              disabled={enriching}
            >
              {enriching ? (
                <span className="flex items-center gap-1">
                  <svg className="h-3 w-3 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Enriching
                </span>
              ) : (
                "Enrich"
              )}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
