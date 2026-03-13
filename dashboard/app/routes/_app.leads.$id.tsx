import { useState } from "react";
import { useParams, useNavigate } from "@remix-run/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { LazyMap } from "~/components/lazy-map";
import { fetchLead, enrichLead } from "~/lib/api";
import { toast } from "~/hooks/use-toast";
import type { LeadDetail } from "~/lib/types";

function statusBadgeClass(status: string): string {
  switch (status) {
    case "enriched": return "bg-green-500/20 text-green-400";
    case "contacted": return "bg-blue-500/20 text-blue-400";
    case "failed": return "bg-red-500/20 text-red-400";
    default: return "bg-zinc-700 text-zinc-400";
  }
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-zinc-400 w-28 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
        <div
          className="h-full bg-orange-500 rounded-full"
          style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
        />
      </div>
      <span className="text-xs text-zinc-400 w-8 text-right">{value}</span>
    </div>
  );
}

interface TimelineEvent {
  label: string;
  date?: string | null;
  done: boolean;
}

function RichTimeline({ events }: { events: TimelineEvent[] }) {
  return (
    <div className="space-y-4">
      {events.map((ev, i) => (
        <div key={i} className="flex items-start gap-3">
          <div className="flex flex-col items-center pt-0.5">
            <div
              className={`h-2.5 w-2.5 rounded-full border-2 shrink-0 ${
                ev.done ? "bg-orange-500 border-orange-500" : "bg-transparent border-zinc-600"
              }`}
            />
            {i < events.length - 1 && (
              <div className={`w-0.5 h-6 mt-1 ${ev.done ? "bg-orange-500/40" : "bg-zinc-800"}`} />
            )}
          </div>
          <div className="flex-1 min-w-0 -mt-0.5">
            <p className={`text-sm ${ev.done ? "text-white font-medium" : "text-zinc-500"}`}>
              {ev.label}
            </p>
            {ev.date && (
              <p className="text-xs text-zinc-600 mt-0.5">
                {new Date(ev.date).toLocaleDateString("en-US", {
                  month: "short",
                  day: "numeric",
                  year: "numeric",
                })}
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function LeadDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [enriching, setEnriching] = useState(false);

  const { data: lead, isLoading, error } = useQuery({
    queryKey: ["lead", id],
    queryFn: () => fetchLead(id!),
    enabled: !!id,
  });

  const handleEnrich = async () => {
    if (!id) return;
    setEnriching(true);
    try {
      await enrichLead(id);
      toast({ title: "Enrichment started", variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["lead", id] });
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

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-3 gap-4">
          <div className="col-span-2 space-y-4">
            <Skeleton className="h-48" />
            <Skeleton className="h-64" />
          </div>
          <Skeleton className="h-full min-h-[300px]" />
        </div>
      </div>
    );
  }

  if (error || !lead) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => navigate("/leads")}>
          &larr; Back to Leads
        </Button>
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error instanceof Error ? error.message : "Lead not found"}
        </div>
      </div>
    );
  }

  const isDiscovered = !!lead.discovered_at;
  const isEnriched = lead.status === "enriched" || lead.status === "contacted";
  const isContacted = lead.status === "contacted";

  const timelineEvents: TimelineEvent[] = [
    { label: "Discovered", date: lead.discovered_at, done: isDiscovered },
    { label: "Enriched", date: isEnriched ? lead.discovered_at : null, done: isEnriched },
    { label: "Contacted", date: isContacted ? lead.discovered_at : null, done: isContacted },
  ];

  const enrichmentData = lead.enrichment_data ?? {};
  const technologies: string[] = Array.isArray(enrichmentData.technologies) ? enrichmentData.technologies : [];
  const competitors: string[] = Array.isArray(enrichmentData.competitors) ? enrichmentData.competitors : [];

  const mapLeads = lead.latitude != null && lead.longitude != null ? [lead] : [];

  const score = lead?.lead_score ?? 0;
  const scoreBreakdown = [
    { label: "Overall Score", value: score },
    { label: "Contact Data", value: lead.email || lead.phone ? 90 : 10 },
    { label: "Social Presence", value: lead.social_links ? Math.min(100, Object.keys(lead.social_links).length * 25) : 0 },
    { label: "Website", value: lead.website ? 70 : 0 },
  ];

  const aiRecs: string[] = (() => {
    // Try analysis_notes from enrichment_data first
    const notes = enrichmentData.analysis_notes;
    if (typeof notes === "string" && notes.trim()) {
      return notes.split("\n").filter((line: string) => line.trim().length > 0);
    }
    // Try recommendations array from enrichment_data
    const recs = enrichmentData.recommendations;
    if (Array.isArray(recs) && recs.length > 0) {
      return recs;
    }
    // Fallback for enriched leads without analysis
    if (isEnriched) {
      return [
        `Send personalized proposal referencing ${lead.category ?? "their"} industry`,
        `Include local market stats for ${lead.city ?? "their city"}`,
        "Mention relevant portfolio projects",
      ];
    }
    return ["Enrich this lead first to unlock AI recommendations"];
  })();

  return (
    <div className="space-y-6">
      {/* Breadcrumb + header */}
      <div>
        <div className="flex items-center gap-2 text-xs text-zinc-500 mb-3">
          <button onClick={() => navigate("/leads")} className="hover:text-zinc-300 transition-colors">
            Leads
          </button>
          <span>/</span>
          <span className="text-zinc-300">{lead.name}</span>
        </div>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-white">{lead.name}</h1>
            <div className="flex items-center gap-2 mt-1.5">
              <span className={`text-[11px] font-medium px-2 py-0.5 rounded ${statusBadgeClass(lead.status)}`}>
                {lead.status}
              </span>
              {lead.temperature && (
                <span className={`px-2 py-0.5 rounded text-xs font-medium uppercase ${
                  lead.temperature === "hot" ? "bg-red-500/20 text-red-400" :
                  lead.temperature === "warm" ? "bg-amber-500/20 text-amber-400" :
                  "bg-zinc-700 text-zinc-400"
                }`}>
                  {lead.temperature}
                </span>
              )}
              {lead.city && (
                <span className="text-sm text-zinc-500">
                  {lead.city}{lead.country ? `, ${lead.country}` : ""}
                </span>
              )}
              {lead.category && (
                <span className="text-sm text-zinc-500">· {lead.category}</span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {lead.status === "new" && (
              <Button
                onClick={handleEnrich}
                disabled={enriching}
                variant="outline"
                size="sm"
                className="border-zinc-700"
              >
                {enriching ? "Enriching..." : "Enrich"}
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              className="border-zinc-700"
              onClick={() => navigate("/outreach")}
            >
              Add to Campaign
            </Button>
            <Button
              size="sm"
              className="bg-orange-500 hover:bg-orange-600 text-white"
              onClick={() => navigate("/outreach")}
            >
              Send Proposal
            </Button>
          </div>
        </div>
      </div>

      {/* 2/3 + 1/3 layout */}
      <div className="grid grid-cols-3 gap-6">
        {/* Left column (2/3) */}
        <div className="col-span-2 space-y-6">
          {/* Contact + Business */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
              <p className="text-sm font-semibold text-white mb-4">Contact Information</p>
              <dl className="space-y-3">
                {lead.email && (
                  <div>
                    <dt className="text-[10px] text-zinc-500 uppercase tracking-wider">Email</dt>
                    <dd className="text-sm mt-0.5">
                      <a href={`mailto:${lead.email}`} className="text-orange-400 hover:underline">{lead.email}</a>
                    </dd>
                  </div>
                )}
                {lead.phone && (
                  <div>
                    <dt className="text-[10px] text-zinc-500 uppercase tracking-wider">Phone</dt>
                    <dd className="text-sm text-white mt-0.5">{lead.phone}</dd>
                  </div>
                )}
                {lead.website && (
                  <div>
                    <dt className="text-[10px] text-zinc-500 uppercase tracking-wider">Website</dt>
                    <dd className="text-sm mt-0.5">
                      <a href={lead.website} target="_blank" rel="noopener noreferrer" className="text-orange-400 hover:underline truncate block">{lead.website}</a>
                    </dd>
                  </div>
                )}
                {lead.address && (
                  <div>
                    <dt className="text-[10px] text-zinc-500 uppercase tracking-wider">Address</dt>
                    <dd className="text-sm text-white mt-0.5">{lead.address}</dd>
                  </div>
                )}
                {!lead.email && !lead.phone && !lead.website && !lead.address && (
                  <p className="text-sm text-zinc-600">No contact info available</p>
                )}
              </dl>
            </div>

            <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
              <p className="text-sm font-semibold text-white mb-4">Business Details</p>
              <dl className="space-y-3">
                {lead.category && (
                  <div>
                    <dt className="text-[10px] text-zinc-500 uppercase tracking-wider">Category</dt>
                    <dd className="text-sm text-white mt-0.5">{lead.category}</dd>
                  </div>
                )}
                {lead.enrichment_source && (
                  <div>
                    <dt className="text-[10px] text-zinc-500 uppercase tracking-wider">Source</dt>
                    <dd className="mt-0.5">
                      <Badge className="bg-zinc-800 text-zinc-300 text-[10px]">{lead.enrichment_source}</Badge>
                    </dd>
                  </div>
                )}
                {lead.google_rating != null && (
                  <div>
                    <dt className="text-[10px] text-zinc-500 uppercase tracking-wider">Google Rating</dt>
                    <dd className="text-sm text-white mt-0.5 flex items-center gap-1.5">
                      <span className="text-amber-400">{"*".repeat(Math.round(lead.google_rating))}</span>
                      <span>{lead.google_rating.toFixed(1)}</span>
                      {lead.review_count != null && (
                        <span className="text-zinc-500">({lead.review_count} reviews)</span>
                      )}
                    </dd>
                  </div>
                )}
                {lead.review_count != null && lead.google_rating == null && (
                  <div>
                    <dt className="text-[10px] text-zinc-500 uppercase tracking-wider">Reviews</dt>
                    <dd className="text-sm text-white mt-0.5">{lead.review_count} reviews</dd>
                  </div>
                )}
                {technologies.length > 0 && (
                  <div>
                    <dt className="text-[10px] text-zinc-500 uppercase tracking-wider">Technologies</dt>
                    <dd className="flex flex-wrap gap-1 mt-1">
                      {technologies.slice(0, 5).map((t: string) => (
                        <span key={t} className="bg-zinc-800 text-zinc-400 text-[10px] px-1.5 py-0.5 rounded">{t}</span>
                      ))}
                    </dd>
                  </div>
                )}
                {lead.social_links && Object.keys(lead.social_links).length > 0 && (
                  <div>
                    <dt className="text-[10px] text-zinc-500 uppercase tracking-wider">Social</dt>
                    <dd className="flex flex-wrap gap-1.5 mt-1">
                      {Object.entries(lead.social_links).map(([platform, url]) => (
                        <a key={platform} href={url} target="_blank" rel="noopener noreferrer" className="text-xs text-orange-400 hover:underline capitalize">{platform}</a>
                      ))}
                    </dd>
                  </div>
                )}
              </dl>
            </div>
          </div>

          {/* AI Recommendations */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
            <p className="text-sm font-semibold text-white mb-4">AI Recommendations</p>
            <ul className="space-y-2">
              {aiRecs.map((rec, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-zinc-300">
                  <span className="text-orange-500 mt-0.5 shrink-0">›</span>
                  {rec}
                </li>
              ))}
            </ul>
          </div>

          {/* Competitors */}
          {competitors.length > 0 && (
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
              <p className="text-sm font-semibold text-white mb-4">Competitors</p>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-zinc-500 text-xs border-b border-zinc-800">
                    <th className="text-left pb-2 font-medium">Name</th>
                  </tr>
                </thead>
                <tbody>
                  {competitors.map((c: string, i: number) => (
                    <tr key={i} className="border-b border-zinc-800/50 last:border-0">
                      <td className="py-2 text-zinc-300">{c}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Map */}
          {mapLeads.length > 0 && (
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
              <div className="px-5 py-3 border-b border-zinc-800 flex items-center justify-between">
                <p className="text-sm font-semibold text-white">Location</p>
                {lead.latitude != null && lead.longitude != null && (
                  <a
                    href={`https://www.openstreetmap.org/?mlat=${lead.latitude}&mlon=${lead.longitude}#map=17/${lead.latitude}/${lead.longitude}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-orange-400 hover:underline"
                  >
                    View on OpenStreetMap
                  </a>
                )}
              </div>
              <LazyMap leads={mapLeads} className="h-[220px]" />
            </div>
          )}
        </div>

        {/* Right column (1/3) */}
        <div className="space-y-6">
          {/* Timeline */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
            <p className="text-sm font-semibold text-white mb-4">Timeline</p>
            <RichTimeline events={timelineEvents} />
          </div>

          {/* Score Breakdown */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
            <p className="text-sm font-semibold text-white mb-4">Lead Score Breakdown</p>
            <div className="space-y-3">
              {scoreBreakdown.map((item) => (
                <ScoreBar key={item.label} label={item.label} value={item.value} />
              ))}
            </div>
          </div>

          {/* Meta */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
            <p className="text-sm font-semibold text-white mb-3">Details</p>
            <dl className="space-y-2 text-xs">
              {lead.discovered_at && (
                <div className="flex justify-between">
                  <dt className="text-zinc-500">Discovered</dt>
                  <dd className="text-zinc-300">
                    {new Date(lead.discovered_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
                  </dd>
                </div>
              )}
              {lead.enrichment_cost != null && (
                <div className="flex justify-between">
                  <dt className="text-zinc-500">Enrich Cost</dt>
                  <dd className="text-zinc-300">${lead.enrichment_cost.toFixed(3)}</dd>
                </div>
              )}
              {lead.h3_index && (
                <div className="flex justify-between">
                  <dt className="text-zinc-500">H3 Index</dt>
                  <dd className="text-zinc-300 font-mono text-[10px]">{lead.h3_index}</dd>
                </div>
              )}
              {lead.osm_id && (
                <div className="flex justify-between">
                  <dt className="text-zinc-500">OSM ID</dt>
                  <dd className="text-zinc-300 font-mono text-[10px]">{lead.osm_id}</dd>
                </div>
              )}
              <div className="flex justify-between">
                <dt className="text-zinc-500">ID</dt>
                <dd className="text-zinc-600 font-mono text-[10px] truncate max-w-[120px]">{lead.id}</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
    </div>
  );
}
