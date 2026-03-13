import { useState } from "react";
import { useNavigate } from "@remix-run/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { LazyMap } from "~/components/lazy-map";
import { fetchLeads, fetchPipelineBStats, startScan } from "~/lib/api";
import { toast } from "~/hooks/use-toast";
import type { Lead } from "~/lib/types";

const CATEGORY_OPTIONS = [
  { value: "auto", label: "Auto" },
  { value: "beauty", label: "Beauty" },
  { value: "food", label: "Food" },
  { value: "medical", label: "Medical" },
  { value: "retail", label: "Retail" },
  { value: "tech", label: "Tech" },
];

function scoreColor(score: number | null): string {
  if (score == null) return "bg-zinc-700";
  if (score >= 80) return "bg-orange-500";
  if (score >= 50) return "bg-yellow-500";
  return "bg-zinc-600";
}

function ResultCard({
  lead,
  onClick,
}: {
  lead: Lead;
  onClick: () => void;
}) {
  const score = Math.round(Math.random() * 40 + 60); // placeholder until API returns score
  return (
    <div
      className="flex items-center gap-3 py-3 px-4 border-b border-zinc-800 last:border-0 cursor-pointer hover:bg-zinc-800/50 transition-colors"
      onClick={onClick}
    >
      <div className="flex-1 min-w-0">
        <p className="font-semibold text-white text-sm truncate">{lead.name}</p>
        <p className="text-xs text-zinc-500 truncate">
          {[lead.category, lead.city].filter(Boolean).join(" / ")}
        </p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <div className="w-16 h-1.5 bg-zinc-700 rounded-full overflow-hidden">
          <div
            className={`h-full ${scoreColor(score)} rounded-full`}
            style={{ width: `${score}%` }}
          />
        </div>
        <span className="text-xs text-zinc-400 w-6 text-right">{score}</span>
      </div>
    </div>
  );
}

export default function GeoScoutPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [city, setCity] = useState("Moscow");
  const [selectedCategories, setSelectedCategories] = useState<Set<string>>(
    new Set(["auto"])
  );
  const [isScanning, setIsScanning] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["geo-leads", city],
    queryFn: () =>
      fetchLeads({
        city: city || undefined,
        limit: 100,
      }),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  const { data: stats } = useQuery({
    queryKey: ["pipeline-b-stats"],
    queryFn: fetchPipelineBStats,
    staleTime: 60_000,
  });

  const leads = data?.leads ?? [];
  const total = data?.total ?? 0;

  const toggleCategory = (cat: string) => {
    setSelectedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) {
        next.delete(cat);
      } else {
        next.add(cat);
      }
      return next;
    });
  };

  const handleScan = async () => {
    if (!city.trim()) {
      toast({ title: "City required", variant: "destructive" });
      return;
    }
    setIsScanning(true);
    try {
      const result = await startScan(city.trim());
      toast({ title: "Scan started", description: result.message, variant: "success" });
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ["geo-leads"] });
        queryClient.invalidateQueries({ queryKey: ["pipeline-b-stats"] });
      }, 3000);
    } catch (err) {
      toast({
        title: "Scan failed",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setIsScanning(false);
    }
  };

  // Map leads to geo points
  const geoLeads = leads.filter((l) => l.latitude && l.longitude);

  return (
    <div className="flex flex-col h-full space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <h1 className="text-2xl font-bold tracking-tight text-white">
          Geo Scanner
        </h1>
        <div className="flex items-center gap-2">
          <Badge className="bg-orange-500/20 text-orange-400 text-xs border border-orange-500/30">
            Grid / Country
          </Badge>
          <Badge className="bg-zinc-800 text-zinc-400 text-xs">
            Mid-Size
          </Badge>
          <Badge className="bg-zinc-800 text-zinc-400 text-xs">
            Telegram
          </Badge>
        </div>
      </div>

      {/* Filter row */}
      <div className="flex items-center gap-4 shrink-0 flex-wrap">
        <Input
          placeholder="Moscow"
          value={city}
          onChange={(e) => setCity(e.target.value)}
          className="w-40 bg-zinc-900 border-zinc-700"
          onKeyDown={(e) => e.key === "Enter" && handleScan()}
        />
        <div className="flex items-center gap-3">
          {CATEGORY_OPTIONS.map((cat) => (
            <label
              key={cat.value}
              className="flex items-center gap-1.5 cursor-pointer text-sm"
            >
              <input
                type="checkbox"
                checked={selectedCategories.has(cat.value)}
                onChange={() => toggleCategory(cat.value)}
                className="accent-orange-500"
              />
              <span className="text-zinc-400">{cat.label}</span>
            </label>
          ))}
        </div>
        <Button
          onClick={handleScan}
          disabled={isScanning || !city.trim()}
          className="bg-orange-500 hover:bg-orange-600 text-white ml-auto"
          size="sm"
        >
          {isScanning ? "Scanning..." : "Start Scan"}
        </Button>
      </div>

      {/* Split layout: Map (65%) + Results panel (35%) */}
      <div className="flex gap-4 flex-1 min-h-0">
        {/* Map */}
        <div className="flex-[0_0_65%] bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden relative">
          {isLoading ? (
            <Skeleton className="h-full w-full" />
          ) : (
            <>
              <LazyMap
                leads={geoLeads}
                onMarkerClick={(id) => navigate(`/leads/${id}`)}
                className="h-full w-full"
              />
              <div className="absolute top-3 left-3 bg-zinc-900/80 text-zinc-400 text-xs px-2 py-1 rounded">
                {city} Region
              </div>
            </>
          )}
        </div>

        {/* Results panel */}
        <div className="flex-[0_0_35%] bg-zinc-900 border border-zinc-800 rounded-lg flex flex-col min-h-0">
          <div className="p-4 border-b border-zinc-800 shrink-0">
            <p className="text-sm font-semibold text-white">
              {total} Results
            </p>
          </div>
          <div className="flex-1 overflow-y-auto">
            {isLoading ? (
              <div className="p-4 space-y-3">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-12" />
                ))}
              </div>
            ) : leads.length === 0 ? (
              <div className="flex items-center justify-center h-32 text-zinc-600 text-sm">
                No results. Start a scan.
              </div>
            ) : (
              leads.map((lead) => (
                <ResultCard
                  key={lead.id}
                  lead={lead}
                  onClick={() => navigate(`/leads/${lead.id}`)}
                />
              ))
            )}
          </div>
        </div>
      </div>

      {/* Bottom status bar */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-lg px-4 py-2 flex items-center gap-4 text-xs text-zinc-500 shrink-0">
        <span>
          Scan:{" "}
          <span className="text-white">
            {stats?.by_status?.enriched ?? 0}/{stats?.total_leads ?? 0}
          </span>
        </span>
        <span className="text-zinc-700">•</span>
        <span>
          {total} total found
        </span>
        {stats?.top_cities && stats.top_cities.length > 0 && (
          <>
            <span className="text-zinc-700">•</span>
            <span>Top: {stats.top_cities[0].city}</span>
          </>
        )}
      </div>
    </div>
  );
}
