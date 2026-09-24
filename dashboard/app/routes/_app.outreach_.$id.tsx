import { useState } from "react";
import { useParams, useNavigate } from "@remix-run/react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Skeleton } from "~/components/ui/skeleton";
import { Separator } from "~/components/ui/separator";
import { Pagination } from "~/components/pagination";
import { MetricCard } from "~/components/metric-card";
import { CampaignEditor } from "~/components/campaign-editor";
import {
  fetchCampaign,
  getCampaignLeads,
  startCampaign,
  updateCampaign,
  deleteCampaign,
} from "~/lib/api";
import { toast } from "~/hooks/use-toast";
import type { CreateCampaignPayload } from "~/lib/types";

const LEADS_PAGE_SIZE = 30;

function statusBadgeVariant(status: string): "success" | "secondary" | "default" | "destructive" | "warning" {
  switch (status) {
    case "active":
    case "sending":
      return "success";
    case "completed":
      return "default";
    case "draft":
      return "secondary";
    case "paused":
      return "warning";
    case "failed":
      return "destructive";
    default:
      return "secondary";
  }
}

function leadStatusVariant(status: string): "success" | "secondary" | "default" | "destructive" | "warning" {
  switch (status) {
    case "sent":
      return "default";
    case "opened":
      return "success";
    case "replied":
      return "success";
    case "bounced":
      return "destructive";
    case "pending":
      return "secondary";
    default:
      return "secondary";
  }
}

export default function CampaignDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [leadsPage, setLeadsPage] = useState(0);

  const campaignId = Number(id);

  const { data: campaign, isLoading, error } = useQuery({
    queryKey: ["campaign", campaignId],
    queryFn: () => fetchCampaign(campaignId),
    enabled: !isNaN(campaignId),
  });

  const { data: leadsData, isLoading: leadsLoading } = useQuery({
    queryKey: ["campaign-leads", campaignId, leadsPage],
    queryFn: () => getCampaignLeads(campaignId, { limit: LEADS_PAGE_SIZE, offset: leadsPage * LEADS_PAGE_SIZE }),
    enabled: !isNaN(campaignId),
  });

  const updateMutation = useMutation({
    mutationFn: (data: Partial<CreateCampaignPayload>) => updateCampaign(campaignId, data),
    onSuccess: () => {
      toast({ title: "Campaign updated", variant: "success" });
      setEditing(false);
      queryClient.invalidateQueries({ queryKey: ["campaign", campaignId] });
    },
    onError: (err) => {
      toast({
        title: "Failed to update",
        description: err instanceof Error ? err.message : "Something went wrong",
        variant: "destructive",
      });
    },
  });

  const handleStart = async () => {
    try {
      await startCampaign(campaignId);
      toast({ title: "Campaign started", variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["campaign", campaignId] });
    } catch (err) {
      toast({
        title: "Failed to start",
        description: err instanceof Error ? err.message : "Something went wrong",
        variant: "destructive",
      });
    }
  };

  const handleDelete = async () => {
    try {
      await deleteCampaign(campaignId);
      toast({ title: "Campaign deleted", variant: "success" });
      navigate("/outreach");
    } catch (err) {
      toast({
        title: "Failed to delete",
        description: err instanceof Error ? err.message : "Something went wrong",
        variant: "destructive",
      });
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

  if (error || !campaign) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => navigate("/outreach")}>
          &larr; Back to Outreach
        </Button>
        <div className="flex items-center justify-between rounded-lg border border-destructive/50 bg-destructive/10 p-4">
          <p className="text-sm text-destructive">
            {error instanceof Error ? error.message : "Campaign not found"}
          </p>
          {error && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => queryClient.invalidateQueries({ queryKey: ["campaign", campaignId] })}
            >
              Retry
            </Button>
          )}
        </div>
      </div>
    );
  }

  const leads = leadsData?.leads ?? [];
  const leadsTotal = leadsData?.total ?? 0;

  // Render template preview with highlighted variables
  const renderPreview = (text: string) => {
    const parts = text.split(/({{[^}]+}})/g);
    return parts.map((part, i) =>
      part.match(/^{{[^}]+}}$/) ? (
        <span key={i} className="bg-primary/20 text-primary font-medium px-0.5 rounded">
          {part}
        </span>
      ) : (
        <span key={i}>{part}</span>
      )
    );
  };

  if (editing) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
          &larr; Back to Campaign
        </Button>
        <h1 className="text-2xl font-semibold tracking-tight">Edit Campaign</h1>
        <CampaignEditor
          initial={{
            name: campaign.name,
            subject: campaign.subject,
            body: campaign.body,
            city_filter: campaign.city_filter ?? undefined,
            category_filter: campaign.category_filter ?? undefined,
          }}
          onSave={async (data) => {
            await updateMutation.mutateAsync(data);
          }}
          onCancel={() => setEditing(false)}
          saving={updateMutation.isPending}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="sm" onClick={() => navigate("/outreach")}>
          &larr; Back
        </Button>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">{campaign.name}</h1>
            <Badge variant={statusBadgeVariant(campaign.status)}>{campaign.status}</Badge>
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            Created {new Date(campaign.created_at).toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {campaign.status === "draft" && (
            <>
              <Button variant="success" onClick={handleStart}>Start</Button>
              <Button variant="outline" onClick={() => setEditing(true)}>Edit</Button>
              <Button variant="destructive" onClick={handleDelete}>Delete</Button>
            </>
          )}
          {(campaign.status === "active" || campaign.status === "sending") && (
            <Badge variant="success" className="text-sm px-3 py-1">Sending in progress</Badge>
          )}
        </div>
      </div>

      {/* Metric cards */}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          icon={
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 2L11 13" /><path d="M22 2l-7 20-4-9-9-4 20-7z" />
            </svg>
          }
          label="Sent"
          value={campaign.sent_count}
        />
        <MetricCard
          icon={
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" />
            </svg>
          }
          label="Opened"
          value={campaign.opened_count}
        />
        <MetricCard
          icon={
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z" />
            </svg>
          }
          label="Replied"
          value={campaign.replied_count}
        />
        <MetricCard
          icon={
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" /><line x1="15" y1="9" x2="9" y2="15" /><line x1="9" y1="9" x2="15" y2="15" />
            </svg>
          }
          label="Bounced"
          value={campaign.bounced_count}
        />
      </div>

      {/* Email template */}
      <Card className="border-border/50">
        <CardHeader className="pb-3">
          <CardTitle className="text-base font-medium">Email Template</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            <div>
              <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Subject</p>
              <p className="text-sm font-medium">{renderPreview(campaign.subject)}</p>
            </div>
            <Separator />
            <div>
              <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Body</p>
              <div className="text-sm whitespace-pre-wrap leading-relaxed bg-muted/30 rounded-md p-4">
                {renderPreview(campaign.body)}
              </div>
            </div>
            {(campaign.city_filter || campaign.category_filter) && (
              <>
                <Separator />
                <div className="flex gap-4 text-xs text-muted-foreground">
                  {campaign.city_filter && <span>City filter: {campaign.city_filter}</span>}
                  {campaign.category_filter && <span>Category filter: {campaign.category_filter}</span>}
                </div>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Leads table */}
      <Card className="border-border/50">
        <CardHeader className="pb-3">
          <CardTitle className="text-base font-medium">
            Campaign Leads
            {leadsTotal > 0 && (
              <span className="text-sm font-normal text-muted-foreground ml-2">({leadsTotal})</span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {leadsLoading && (
            <div className="p-4">
              <Skeleton className="h-[200px]" />
            </div>
          )}

          {!leadsLoading && leads.length === 0 && (
            <div className="p-8 text-center text-sm text-muted-foreground">
              No leads in this campaign
            </div>
          )}

          {leads.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border/50">
                    <th className="text-left py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Business</th>
                    <th className="text-left py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Email</th>
                    <th className="text-left py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Status</th>
                    <th className="text-left py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Sent At</th>
                    <th className="text-left py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Opened At</th>
                  </tr>
                </thead>
                <tbody>
                  {leads.map((lead) => (
                    <tr key={lead.id} className="border-b border-border/30 hover:bg-accent/30 transition-colors">
                      <td className="py-2.5 px-4 font-medium">{lead.business_name}</td>
                      <td className="py-2.5 px-4 text-muted-foreground">{lead.email}</td>
                      <td className="py-2.5 px-4">
                        <Badge variant={leadStatusVariant(lead.status)}>{lead.status}</Badge>
                      </td>
                      <td className="py-2.5 px-4 text-xs text-muted-foreground">
                        {lead.sent_at ? new Date(lead.sent_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "--"}
                      </td>
                      <td className="py-2.5 px-4 text-xs text-muted-foreground">
                        {lead.opened_at ? new Date(lead.opened_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "--"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Pagination
        page={leadsPage}
        pageSize={LEADS_PAGE_SIZE}
        total={leadsTotal}
        onPageChange={setLeadsPage}
      />
    </div>
  );
}
