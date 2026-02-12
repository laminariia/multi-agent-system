import { useParams, useNavigate } from "@remix-run/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Separator } from "~/components/ui/separator";
import { Skeleton } from "~/components/ui/skeleton";
import { fetchLead } from "~/lib/api";

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

export default function LeadDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: lead, isLoading, error } = useQuery({
    queryKey: ["lead", id],
    queryFn: () => fetchLead(id!),
    enabled: !!id,
  });

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
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Contact info */}
        <Card className="border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-medium">Contact Information</CardTitle>
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
            <CardTitle className="text-base font-medium">Business Details</CardTitle>
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

      {/* Location */}
      {(lead.latitude != null || lead.longitude != null) && (
        <Card className="border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-medium">Location</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-6 text-sm">
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
          </CardContent>
        </Card>
      )}

      {/* Social links */}
      {lead.social_links && Object.keys(lead.social_links).length > 0 && (
        <Card className="border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-medium">Social Links</CardTitle>
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

      {/* Enrichment data */}
      {lead.enrichment_data && Object.keys(lead.enrichment_data).length > 0 && (
        <Card className="border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-medium">Enrichment Data</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="text-xs font-mono bg-muted/50 rounded-md p-3 overflow-auto max-h-[300px]">
              {JSON.stringify(lead.enrichment_data, null, 2)}
            </pre>
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
