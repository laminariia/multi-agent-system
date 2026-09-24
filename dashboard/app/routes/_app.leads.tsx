import { useState } from "react";
import { useNavigate } from "@remix-run/react";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { Pagination } from "~/components/pagination";
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

const categoryFilters = [
  { value: "all", label: "All Categories" },
  { value: "auto", label: "Auto" },
  { value: "medical", label: "Medical" },
  { value: "restaurants", label: "Restaurants" },
  { value: "beauty", label: "Beauty" },
  { value: "furniture", label: "Furniture" },
  { value: "other", label: "Other" },
] as const;

const sortOptions = [
  { value: "newest", label: "Newest" },
  { value: "temperature", label: "Temperature" },
  { value: "score", label: "Score" },
] as const;

function temperatureLabel(lead: Lead): "Hot" | "Warm" | "Cold" {
  const score = lead.lead_score ?? 0;
  if (score >= 5) return "Hot";
  if (score >= 3) return "Warm";
  return "Cold";
}

function TemperatureBadge({ temp }: { temp: "Hot" | "Warm" | "Cold" }) {
  const styles = {
    Hot: "bg-red-500 text-white",
    Warm: "bg-orange-500 text-white",
    Cold: "bg-blue-500/20 text-blue-400 border border-blue-500/30",
  };
  return (
    <span className={`text-[10px] font-medium px-2 py-0.5 rounded ${styles[temp]}`}>
      {temp}
    </span>
  );
}

export default function LeadsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("all");
  const [cityFilter, setCityFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [sortBy, setSortBy] = useState("newest");
  const [scanCity, setScanCity] = useState("");
  const [isScanning, setIsScanning] = useState(false);
  const [page, setPage] = useState(0);

  const handleStatusChange = (v: string) => { setStatusFilter(v); setPage(0); };
  const handleCategoryChange = (v: string) => { setCategoryFilter(v); setPage(0); };
  const handleSortChange = (v: string) => { setSortBy(v); setPage(0); };

  const { data, isLoading, error } = useQuery({
    queryKey: ["leads", statusFilter, cityFilter, categoryFilter, sortBy, page],
    queryFn: () =>
      fetchLeads({
        status: statusFilter === "all" ? undefined : statusFilter,
        city: cityFilter || undefined,
        category: categoryFilter === "all" ? undefined : categoryFilter,
        sort: sortBy !== "newest" ? sortBy : undefined,
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
      toast({ title: "City required", description: "Please enter a city name to scan.", variant: "destructive" });
      return;
    }
    setIsScanning(true);
    try {
      const result = await startScan(city);
      toast({ title: "Scan started", description: result.message, variant: "success" });
      setScanCity("");
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ["leads"] });
        queryClient.invalidateQueries({ queryKey: ["pipeline-b-stats"] });
      }, 3000);
    } catch (err) {
      toast({ title: "Scan failed", description: err instanceof Error ? err.message : "Something went wrong", variant: "destructive" });
    } finally {
      setIsScanning(false);
    }
  };

  const leads = data?.leads ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold tracking-tight text-white">Leads</h1>
        <div className="flex items-center gap-2">
          <Input
            placeholder="Enter city to scan..."
            value={scanCity}
            onChange={(e) => setScanCity(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleScan(); }}
            className="w-48 bg-zinc-900 border-zinc-700"
          />
          <Button
            onClick={handleScan}
            disabled={isScanning || !scanCity.trim()}
            className="bg-orange-500 hover:bg-orange-600 text-white"
            size="sm"
          >
            {isScanning ? "Scanning..." : "Start Scan"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="border-zinc-700"
            disabled={leads.length === 0}
            onClick={() => {
              const rows = leads.map((l) => ({
                name: l.name,
                category: l.category ?? "",
                city: l.city ?? "",
                phone: l.phone ?? "",
                email: l.email ?? "",
                status: l.status,
                discovered_at: l.discovered_at ?? "",
              }));
              downloadCSV(rows, `leads-${new Date().toISOString().slice(0, 10)}.csv`);
            }}
          >
            Export CSV
          </Button>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Total Leads", value: stats?.total_leads ?? 0 },
          { label: "Enriched", value: stats?.by_status?.["enriched"] ?? 0 },
          { label: "Contacted", value: stats?.by_status?.["contacted"] ?? 0 },
          { label: "New", value: stats?.by_status?.["new"] ?? 0 },
        ].map(({ label, value }) => (
          <div key={label} className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
            <p className="text-xs text-zinc-500 mb-1">{label}</p>
            <p className="text-2xl font-bold text-white">{value}</p>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <Tabs value={statusFilter} onValueChange={handleStatusChange}>
          <TabsList className="bg-zinc-900 border border-zinc-800">
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
          className="w-48 bg-zinc-900 border-zinc-700"
        />
        <Select value={categoryFilter} onValueChange={handleCategoryChange}>
          <SelectTrigger className="w-44 bg-zinc-900 border-zinc-700">
            <SelectValue placeholder="All Categories" />
          </SelectTrigger>
          <SelectContent>
            {categoryFilters.map((c) => (
              <SelectItem key={c.value} value={c.value}>
                {c.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={sortBy} onValueChange={handleSortChange}>
          <SelectTrigger className="w-36 bg-zinc-900 border-zinc-700">
            <SelectValue placeholder="Sort by" />
          </SelectTrigger>
          <SelectContent>
            {sortOptions.map((s) => (
              <SelectItem key={s.value} value={s.value}>
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load leads: {error instanceof Error ? error.message : "Unknown error"}
        </div>
      )}

      {/* Loading */}
      {isLoading && !data && (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !error && leads.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <p className="text-zinc-400 text-base font-medium">No leads found</p>
          <p className="text-zinc-600 text-sm mt-1">Start a geo scan to discover local businesses.</p>
        </div>
      )}

      {/* Leads list (full-width rows) */}
      {leads.length > 0 && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
          {leads.map((lead) => (
            <LeadRow
              key={lead.id}
              lead={lead}
              onClick={() => navigate(`/leads/${lead.id}`)}
            />
          ))}
        </div>
      )}

      <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
    </div>
  );
}

function LeadRow({ lead, onClick }: { lead: Lead; onClick?: () => void }) {
  const queryClient = useQueryClient();
  const [enriching, setEnriching] = useState(false);
  const temp = temperatureLabel(lead);

  const handleEnrich = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setEnriching(true);
    try {
      await enrichLead(lead.id);
      toast({ title: "Enrichment started", description: `Enriching ${lead.name}`, variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["leads"] });
      queryClient.invalidateQueries({ queryKey: ["pipeline-b-stats"] });
    } catch (err) {
      toast({ title: "Enrichment failed", description: err instanceof Error ? err.message : "Something went wrong", variant: "destructive" });
    } finally {
      setEnriching(false);
    }
  };

  return (
    <div
      className={`flex items-center gap-4 px-5 py-4 border-b border-zinc-800 last:border-0 hover:bg-zinc-800/40 transition-colors cursor-pointer ${
        temp === "Hot"
          ? "bg-red-500/5 border-l-2 border-l-red-500"
          : temp === "Warm"
          ? "bg-amber-500/5 border-l-2 border-l-amber-500"
          : ""
      }`}
      onClick={onClick}
    >
      <div className="flex-1 min-w-0">
        <p className="font-semibold text-white text-sm">{lead.name}</p>
        <p className="text-xs text-zinc-500 mt-0.5">
          {[lead.city, lead.category]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <TemperatureBadge temp={temp} />
        {lead.status === "new" && (
          <Button
            size="sm"
            className="text-xs h-7 bg-orange-500 hover:bg-orange-600 text-white"
            onClick={handleEnrich}
            disabled={enriching}
          >
            {enriching ? "Enriching..." : "Enrich"}
          </Button>
        )}
      </div>
    </div>
  );
}
