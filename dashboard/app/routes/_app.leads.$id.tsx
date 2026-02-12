import { useState } from "react";
import { useParams, useNavigate } from "@remix-run/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Separator } from "~/components/ui/separator";
import { Skeleton } from "~/components/ui/skeleton";
import { LazyMap } from "~/components/lazy-map";
import { fetchLead, enrichLead } from "~/lib/api";
import { toast } from "~/hooks/use-toast";
import type { LeadDetail } from "~/lib/types";

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

// Timeline step component
function TimelineStep({ label, done, last }: { label: string; done: boolean; last?: boolean }) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex flex-col items-center">
        <div className={`h-3 w-3 rounded-full border-2 ${done ? "bg-primary border-primary" : "bg-background border-border"}`} />
        {!last && <div className={`w-0.5 h-8 ${done ? "bg-primary/50" : "bg-border/50"}`} />}
      </div>
      <span className={`text-sm -mt-0.5 ${done ? "text-foreground font-medium" : "text-muted-foreground"}`}>
        {label}
      </span>
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
        <Skeleton className="h-[400px]" />
      </div>
    );
  }

  if (error || !lead) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => navigate("/leads")}>
          &larr; Back to Leads
        </Button>
        <div className="flex items-center justify-between rounded-lg border border-destructive/50 bg-destructive/10 p-4">
          <p className="text-sm text-destructive">
            {error instanceof Error ? error.message : "Lead not found"}
          </p>
          {error && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => queryClient.invalidateQueries({ queryKey: ["lead", id] })}
            >
              Retry
            </Button>
          )}
        </div>
      </div>
    );
  }

  // Determine timeline status
  const isDiscovered = !!lead.discovered_at;
  const isEnriched = lead.status === "enriched" || lead.status === "contacted";
  const isContacted = lead.status === "contacted";

  // Extract structured enrichment data
  const enrichmentData = lead.enrichment_data ?? {};
  const hasEnrichmentData = Object.keys(enrichmentData).length > 0;

  // Build fake lead array for mini-map
  const mapLeads = (lead.latitude != null && lead.longitude != null)
    ? [{
        ...lead,
      }]
    : [];

  return (
    <div className="space-y-6">
      {/* Back button + header */}
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="sm" onClick={() => navigate("/leads")}>
          &larr; Back
        </Button>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold tracking-tight">{lead.name}</h1>
          <div className="flex items-center gap-2 mt-1">
            <Badge variant={statusBadgeVariant(lead.status)}>{lead.status}</Badge>
            {lead.city && (
              <span className="text-sm text-muted-foreground">{lead.city}{lead.country ? `, ${lead.country}` : ""}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {lead.status === "new" && (
            <Button onClick={handleEnrich} disabled={enriching}>
              {enriching ? "Enriching..." : "Enrich"}
            </Button>
          )}
          <Button
            variant="outline"
            onClick={() => navigate("/outreach")}
          >
            Add to Campaign
          </Button>
        </div>
      </div>

      {/* Timeline */}
      <Card className="border-border/50">
        <CardHeader className="pb-3">
          <CardTitle className="text-base font-medium">Lead Timeline</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-8">
            <TimelineStep label="Discovered" done={isDiscovered} />
            <TimelineStep label="Enriched" done={isEnriched} />
            <TimelineStep label="Contacted" done={isContacted} last />
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Contact info */}
        <Card className="border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-medium flex items-center gap-2">
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" />
                <circle cx="12" cy="7" r="4" />
              </svg>
              Contact Information
            </CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-3">
              {lead.email && (
                <div>
                  <dt className="text-xs text-muted-foreground uppercase tracking-wider">Email</dt>
                  <dd className="text-sm mt-0.5">
                    <a href={`mailto:${lead.email}`} className="text-primary hover:underline">{lead.email}</a>
                  </dd>
                </div>
              )}
              {lead.phone && (
                <div>
                  <dt className="text-xs text-muted-foreground uppercase tracking-wider">Phone</dt>
                  <dd className="text-sm mt-0.5">{lead.phone}</dd>
                </div>
              )}
              {lead.website && (
                <div>
                  <dt className="text-xs text-muted-foreground uppercase tracking-wider">Website</dt>
                  <dd className="text-sm mt-0.5">
                    <a href={lead.website} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">{lead.website}</a>
                  </dd>
                </div>
              )}
              {lead.address && (
                <div>
                  <dt className="text-xs text-muted-foreground uppercase tracking-wider">Address</dt>
                  <dd className="text-sm mt-0.5">{lead.address}</dd>
                </div>
              )}
              {!lead.email && !lead.phone && !lead.website && !lead.address && (
                <p className="text-sm text-muted-foreground">No contact information available</p>
              )}
            </dl>
          </CardContent>
        </Card>

        {/* Business details */}
        <Card className="border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-medium flex items-center gap-2">
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="7" width="20" height="14" rx="2" ry="2" />
                <path d="M16 21V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v16" />
              </svg>
              Business Details
            </CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-3">
              {lead.category && (
                <div>
                  <dt className="text-xs text-muted-foreground uppercase tracking-wider">Category</dt>
                  <dd className="text-sm mt-0.5">{lead.category}</dd>
                </div>
              )}
              {lead.enrichment_source && (
                <div>
                  <dt className="text-xs text-muted-foreground uppercase tracking-wider">Enrichment Source</dt>
                  <dd className="text-sm mt-0.5">
                    <Badge variant="secondary">{lead.enrichment_source}</Badge>
                  </dd>
                </div>
              )}
              {lead.enrichment_cost != null && (
                <div>
                  <dt className="text-xs text-muted-foreground uppercase tracking-wider">Enrichment Cost</dt>
                  <dd className="text-sm mt-0.5">${lead.enrichment_cost.toFixed(3)}</dd>
                </div>
              )}
              {lead.osm_id && (
                <div>
                  <dt className="text-xs text-muted-foreground uppercase tracking-wider">OSM ID</dt>
                  <dd className="text-sm mt-0.5 font-mono text-xs">{lead.osm_id}</dd>
                </div>
              )}
              {lead.h3_index && (
                <div>
                  <dt className="text-xs text-muted-foreground uppercase tracking-wider">H3 Index</dt>
                  <dd className="text-sm mt-0.5 font-mono text-xs">{lead.h3_index}</dd>
                </div>
              )}
            </dl>
          </CardContent>
        </Card>
      </div>

      {/* Social links */}
      {lead.social_links && Object.keys(lead.social_links).length > 0 && (
        <Card className="border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-medium flex items-center gap-2">
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
                <polyline points="15 3 21 3 21 9" />
                <line x1="10" y1="14" x2="21" y2="3" />
              </svg>
              Social Media
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {Object.entries(lead.social_links).map(([platform, url]) => (
                <a
                  key={platform}
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-md border border-border/50 px-3 py-1.5 text-xs hover:bg-accent/50 transition-colors"
                >
                  <span className="capitalize font-medium">{platform}</span>
                </a>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Structured enrichment data */}
      {hasEnrichmentData && (
        <div className="grid gap-6 lg:grid-cols-3">
          {/* Contact enrichment */}
          {(enrichmentData.emails || enrichmentData.phones || enrichmentData.contact) && (
            <Card className="border-border/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-medium">Contact Data</CardTitle>
              </CardHeader>
              <CardContent>
                <dl className="space-y-2 text-sm">
                  {enrichmentData.emails && Array.isArray(enrichmentData.emails) && (
                    <div>
                      <dt className="text-xs text-muted-foreground uppercase">Emails</dt>
                      {enrichmentData.emails.map((e: string, i: number) => (
                        <dd key={i} className="mt-0.5">
                          <a href={`mailto:${e}`} className="text-primary hover:underline text-xs">{e}</a>
                        </dd>
                      ))}
                    </div>
                  )}
                  {enrichmentData.phones && Array.isArray(enrichmentData.phones) && (
                    <div>
                      <dt className="text-xs text-muted-foreground uppercase">Phones</dt>
                      {enrichmentData.phones.map((p: string, i: number) => (
                        <dd key={i} className="mt-0.5 text-xs">{p}</dd>
                      ))}
                    </div>
                  )}
                  {enrichmentData.contact && typeof enrichmentData.contact === "object" && (
                    <div>
                      {Object.entries(enrichmentData.contact as Record<string, string>).map(([k, v]) => (
                        <div key={k} className="mt-1">
                          <dt className="text-xs text-muted-foreground uppercase">{k}</dt>
                          <dd className="text-xs mt-0.5">{String(v)}</dd>
                        </div>
                      ))}
                    </div>
                  )}
                </dl>
              </CardContent>
            </Card>
          )}

          {/* Domain enrichment */}
          {(enrichmentData.domain || enrichmentData.technologies || enrichmentData.website_info) && (
            <Card className="border-border/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-medium">Domain Info</CardTitle>
              </CardHeader>
              <CardContent>
                <dl className="space-y-2 text-sm">
                  {enrichmentData.domain && (
                    <div>
                      <dt className="text-xs text-muted-foreground uppercase">Domain</dt>
                      <dd className="text-xs mt-0.5">{String(enrichmentData.domain)}</dd>
                    </div>
                  )}
                  {enrichmentData.technologies && Array.isArray(enrichmentData.technologies) && (
                    <div>
                      <dt className="text-xs text-muted-foreground uppercase">Technologies</dt>
                      <dd className="flex flex-wrap gap-1 mt-1">
                        {enrichmentData.technologies.map((t: string, i: number) => (
                          <Badge key={i} variant="secondary" className="text-[10px]">{t}</Badge>
                        ))}
                      </dd>
                    </div>
                  )}
                </dl>
              </CardContent>
            </Card>
          )}

          {/* Other enrichment data */}
          {Object.keys(enrichmentData).filter(
            (k) => !["emails", "phones", "contact", "domain", "technologies", "website_info"].includes(k)
          ).length > 0 && (
            <Card className="border-border/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-medium">Additional Data</CardTitle>
              </CardHeader>
              <CardContent>
                <pre className="text-xs font-mono bg-muted/50 rounded-md p-3 overflow-auto max-h-[200px]">
                  {JSON.stringify(
                    Object.fromEntries(
                      Object.entries(enrichmentData).filter(
                        ([k]) => !["emails", "phones", "contact", "domain", "technologies", "website_info"].includes(k)
                      )
                    ),
                    null,
                    2
                  )}
                </pre>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Mini map */}
      {mapLeads.length > 0 && (
        <Card className="border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-medium flex items-center gap-2">
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                <path d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              Location
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-6 text-sm mb-3">
              {lead.latitude != null && (
                <div>
                  <span className="text-muted-foreground">Lat: </span>
                  <span className="font-mono">{lead.latitude.toFixed(6)}</span>
                </div>
              )}
              {lead.longitude != null && (
                <div>
                  <span className="text-muted-foreground">Lng: </span>
                  <span className="font-mono">{lead.longitude.toFixed(6)}</span>
                </div>
              )}
              {lead.latitude != null && lead.longitude != null && (
                <a
                  href={`https://www.openstreetmap.org/?mlat=${lead.latitude}&mlon=${lead.longitude}#map=17/${lead.latitude}/${lead.longitude}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline text-xs"
                >
                  View on OpenStreetMap
                </a>
              )}
            </div>
            <LazyMap leads={mapLeads} className="h-[250px]" />
          </CardContent>
        </Card>
      )}

      <Separator />

      {/* Meta */}
      <div className="text-xs text-muted-foreground/70">
        {lead.discovered_at && (
          <span>
            Discovered{" "}
            {new Date(lead.discovered_at).toLocaleDateString(undefined, {
              year: "numeric",
              month: "long",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        )}
        {lead.id && <span className="ml-4 font-mono">ID: {lead.id}</span>}
      </div>
    </div>
  );
}
