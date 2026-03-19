import { useState, useMemo } from "react";
import { useNavigate } from "@remix-run/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { ScrollArea } from "~/components/ui/scroll-area";
import { LazyHexMap } from "~/components/lazy-hex-map";
import { fetchLeads, fetchPipelineBStats, startScan } from "~/lib/api";
import { toast } from "~/hooks/use-toast";
import type { Lead } from "~/lib/types";

/* ---------- constants ---------- */

const CATEGORY_OPTIONS = [
  { value: "all", label: "All" },
  { value: "auto", label: "Auto" },
  { value: "beauty", label: "Beauty" },
  { value: "food", label: "Food" },
  { value: "medical", label: "Medical" },
  { value: "retail", label: "Retail" },
  { value: "tech", label: "Tech" },
  { value: "education", label: "Education" },
  { value: "fitness", label: "Fitness" },
  { value: "legal", label: "Legal" },
  { value: "real_estate", label: "Real Estate" },
];

/* ---------- temperature helpers ---------- */

function getTemperature(lead: Lead): string {
  const temp = (lead as unknown as Record<string, unknown>).temperature as string | null;
  if (temp) return temp;
  const score = lead.lead_score ?? 0;
  if (score >= 80) return "hot";
  if (score >= 50) return "warm";
  return "cold";
}

const TEMP_BADGE: Record<string, string> = {
  hot: "bg-red-500/20 text-red-400 border-red-500/30",
  warm: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  cold: "bg-blue-500/20 text-blue-400 border-blue-500/30",
};

/* ---------- score color ---------- */

function scoreColor(score: number | null): string {
  if (score == null) return "bg-zinc-700";
  if (score >= 80) return "bg-red-500";
  if (score >= 50) return "bg-yellow-500";
  return "bg-blue-500";
}

/* ---------- lead result card ---------- */

function ResultCard({
  lead,
  onClick,
}: {
  lead: Lead;
  onClick: () => void;
}) {
  const score = lead.lead_score ?? 0;
  const temp = getTemperature(lead);

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
        <Badge
          className={`text-[9px] border capitalize px-1.5 py-0 ${TEMP_BADGE[temp] ?? TEMP_BADGE.cold}`}
        >
          {temp}
        </Badge>
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

/* ---------- hex lead detail panel ---------- */

function HexLeadPanel({
  leads,
  onClose,
  onLeadClick,
}: {
  leads: Lead[];
  onClose: () => void;
  onLeadClick: (id: string) => void;
}) {
  const hot = leads.filter((l) => getTemperature(l) === "hot").length;
  const warm = leads.filter((l) => getTemperature(l) === "warm").length;
  const cold = leads.filter((l) => getTemperature(l) === "cold").length;

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg flex flex-col max-h-[300px]">
      <div className="p-3 border-b border-zinc-800 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-white">
            {leads.length} leads in area
          </span>
          <div className="flex items-center gap-1">
            {hot > 0 && (
              <Badge className="text-[9px] bg-red-500/20 text-red-400 border border-red-500/30 px-1.5 py-0">
                {hot} hot
              </Badge>
            )}
            {warm > 0 && (
              <Badge className="text-[9px] bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 px-1.5 py-0">
                {warm} warm
              </Badge>
            )}
            {cold > 0 && (
              <Badge className="text-[9px] bg-blue-500/20 text-blue-400 border border-blue-500/30 px-1.5 py-0">
                {cold} cold
              </Badge>
            )}
          </div>
        </div>
        <button
          onClick={onClose}
          className="text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          <svg
            className="h-4 w-4"
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
        </button>
      </div>
      <ScrollArea className="flex-1">
        {leads.map((lead) => (
          <ResultCard
            key={lead.id}
            lead={lead}
            onClick={() => onLeadClick(lead.id)}
          />
        ))}
      </ScrollArea>
    </div>
  );
}

/* ---------- legend ---------- */

function MapLegend() {
  return (
    <div className="absolute bottom-3 left-3 z-[1000] bg-zinc-900/90 border border-zinc-800 rounded-lg p-3 space-y-2">
      <p className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider">
        Temperature
      </p>
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-sm bg-red-500/60" />
          <span className="text-xs text-zinc-300">Hot (score 80+)</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-sm bg-yellow-500/50" />
          <span className="text-xs text-zinc-300">Warm (score 50-79)</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-sm bg-blue-500/40" />
          <span className="text-xs text-zinc-300">Cold (score &lt;50)</span>
        </div>
      </div>
      <p className="text-[10px] text-zinc-500 mt-1">
        Opacity = lead density
      </p>
    </div>
  );
}

/* ---------- page ---------- */

export default function GeoScoutPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [city, setCity] = useState("Moscow");
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [isScanning, setIsScanning] = useState(false);
  const [hexLeads, setHexLeads] = useState<Lead[] | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["geo-leads", city, selectedCategory],
    queryFn: () =>
      fetchLeads({
        city: city || undefined,
        category:
          selectedCategory !== "all" ? selectedCategory : undefined,
        limit: 200,
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

  // Temperature distribution
  const tempDist = useMemo(() => {
    const dist = { hot: 0, warm: 0, cold: 0 };
    for (const lead of leads) {
      const temp = getTemperature(lead);
      if (temp in dist) dist[temp as keyof typeof dist]++;
    }
    return dist;
  }, [leads]);

  const handleScan = async () => {
    if (!city.trim()) {
      toast({ title: "City required", variant: "destructive" });
      return;
    }
    setIsScanning(true);
    try {
      const result = await startScan(city.trim());
      toast({
        title: "Scan started",
        description: result.message,
        variant: "success",
      });
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

  const handleHexClick = (cellLeads: Lead[]) => {
    setHexLeads(cellLeads);
  };

  return (
    <div className="flex flex-col h-full space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <h1 className="text-2xl font-bold tracking-tight text-white">
          Geo Map
        </h1>
        <div className="flex items-center gap-3">
          {/* Temperature summary */}
          <div className="flex items-center gap-2">
            <Badge className="text-[10px] bg-red-500/20 text-red-400 border border-red-500/30">
              {tempDist.hot} hot
            </Badge>
            <Badge className="text-[10px] bg-yellow-500/20 text-yellow-400 border border-yellow-500/30">
              {tempDist.warm} warm
            </Badge>
            <Badge className="text-[10px] bg-blue-500/20 text-blue-400 border border-blue-500/30">
              {tempDist.cold} cold
            </Badge>
          </div>
        </div>
      </div>

      {/* Filter row */}
      <div className="flex items-center gap-4 shrink-0 flex-wrap">
        <Input
          placeholder="City"
          value={city}
          onChange={(e) => setCity(e.target.value)}
          className="w-40 bg-zinc-900 border-zinc-700"
          onKeyDown={(e) => e.key === "Enter" && handleScan()}
        />

        {/* Category filter sidebar (inline) */}
        <div className="flex items-center gap-2 flex-wrap">
          {CATEGORY_OPTIONS.map((cat) => (
            <button
              key={cat.value}
              onClick={() => setSelectedCategory(cat.value)}
              className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                selectedCategory === cat.value
                  ? "bg-orange-500/20 text-orange-400 border-orange-500/50"
                  : "bg-zinc-900 text-zinc-400 border-zinc-700 hover:border-zinc-500"
              }`}
            >
              {cat.label}
            </button>
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

      {/* Map with H3 hexagons */}
      <div className="flex-1 min-h-0 relative">
        {isLoading ? (
          <Skeleton className="h-full w-full rounded-lg" />
        ) : (
          <>
            <LazyHexMap
              leads={leads}
              onHexClick={handleHexClick}
              selectedCategory={selectedCategory}
              className="h-full w-full"
            />
            {/* City label */}
            <div className="absolute top-3 right-3 z-[1000] bg-zinc-900/80 text-zinc-400 text-xs px-2 py-1 rounded">
              {city} Region -- {total} leads
            </div>
            {/* Legend */}
            <MapLegend />
          </>
        )}
      </div>

      {/* Hex click detail panel */}
      {hexLeads && hexLeads.length > 0 && (
        <div className="shrink-0">
          <HexLeadPanel
            leads={hexLeads}
            onClose={() => setHexLeads(null)}
            onLeadClick={(id) => navigate(`/leads/${id}`)}
          />
        </div>
      )}

      {/* Bottom status bar */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-lg px-4 py-2 flex items-center gap-4 text-xs text-zinc-500 shrink-0">
        <span>
          Scan:{" "}
          <span className="text-white">
            {stats?.by_status?.enriched ?? 0}/{stats?.total_leads ?? 0}
          </span>
        </span>
        <span className="text-zinc-700">|</span>
        <span>{total} total found</span>
        {stats?.top_cities && stats.top_cities.length > 0 && (
          <>
            <span className="text-zinc-700">|</span>
            <span>Top: {stats.top_cities[0].city}</span>
          </>
        )}
        <span className="text-zinc-700">|</span>
        <span>
          H3 hexagons: temperature-coded density map
        </span>
      </div>
    </div>
  );
}
