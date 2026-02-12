import { useState } from "react";
import { useNavigate } from "@remix-run/react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "~/components/ui/tabs";
import { Pagination } from "~/components/pagination";
import { MetricCard } from "~/components/metric-card";
import { CampaignEditor } from "~/components/campaign-editor";
import {
  fetchHITLPending,
  resolveHITL,
  fetchCampaigns,
  createCampaign,
  deleteCampaign,
  startCampaign,
} from "~/lib/api";
import { toast } from "~/hooks/use-toast";
import { relativeTime } from "~/lib/utils";
import type { HITLItem, EmailCampaign, CreateCampaignPayload } from "~/lib/types";
import { useAuthStore } from "~/stores/auth-store";

const PAGE_SIZE = 24;

function campaignStatusVariant(status: string): "success" | "secondary" | "default" | "destructive" | "warning" {
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

export default function OutreachPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const [activeTab, setActiveTab] = useState("campaigns");
  const [showEditor, setShowEditor] = useState(false);
  const [campaignPage, setCampaignPage] = useState(0);
  const [pendingPage, setPendingPage] = useState(0);

  // --- Campaigns ---
  const { data: campaignsData, isLoading: campaignsLoading } = useQuery({
    queryKey: ["campaigns", campaignPage],
    queryFn: () => fetchCampaigns({ limit: PAGE_SIZE, offset: campaignPage * PAGE_SIZE }),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  // --- Pending Review (existing HITL email approval) ---
  const { data: pendingData, isLoading: pendingLoading, error: pendingError } = useQuery({
    queryKey: ["outreach-pending", pendingPage],
    queryFn: () =>
      fetchHITLPending({
        type: "email_approval",
        limit: PAGE_SIZE,
        offset: pendingPage * PAGE_SIZE,
      }),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  const createMutation = useMutation({
    mutationFn: (data: CreateCampaignPayload) => createCampaign(data),
    onSuccess: () => {
      toast({ title: "Campaign created", variant: "success" });
      setShowEditor(false);
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
    },
    onError: (err) => {
      toast({
        title: "Failed to create campaign",
        description: err instanceof Error ? err.message : "Something went wrong",
        variant: "destructive",
      });
    },
  });

  const handleResolve = async (id: string, action: string) => {
    try {
      const result = await resolveHITL(id, action);
      toast({
        title: "Action completed",
        description: `${action} - ${result.next_action || "Done"}`,
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["outreach-pending"] });
      queryClient.invalidateQueries({ queryKey: ["hitl-pending"] });
      queryClient.invalidateQueries({ queryKey: ["hitl-pending-count"] });
    } catch (err) {
      toast({
        title: "Action failed",
        description:
          err instanceof Error ? err.message : "Something went wrong",
        variant: "destructive",
      });
    }
  };

  const handleDeleteCampaign = async (id: number) => {
    try {
      await deleteCampaign(id);
      toast({ title: "Campaign deleted", variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
    } catch (err) {
      toast({
        title: "Failed to delete campaign",
        description: err instanceof Error ? err.message : "Something went wrong",
        variant: "destructive",
      });
    }
  };

  const handleStartCampaign = async (id: number) => {
    try {
      await startCampaign(id);
      toast({ title: "Campaign started", variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
    } catch (err) {
      toast({
        title: "Failed to start campaign",
        description: err instanceof Error ? err.message : "Something went wrong",
        variant: "destructive",
      });
    }
  };

  const campaigns = campaignsData?.campaigns ?? [];
  const campaignsTotal = campaignsData?.total ?? 0;
  const pendingItems = pendingData?.items ?? [];
  const pendingTotal = pendingData?.total ?? 0;

  // Aggregate stats from campaigns for Analytics tab
  const totalSent = campaigns.reduce((s, c) => s + c.sent_count, 0);
  const totalOpened = campaigns.reduce((s, c) => s + c.opened_count, 0);
  const totalReplied = campaigns.reduce((s, c) => s + c.replied_count, 0);
  const totalBounced = campaigns.reduce((s, c) => s + c.bounced_count, 0);

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">Outreach</h1>
          {pendingTotal > 0 && (
            <Badge variant="secondary" className="text-sm">
              {pendingTotal} pending
            </Badge>
          )}
        </div>
        {activeTab === "campaigns" && !showEditor && (
          <Button onClick={() => setShowEditor(true)}>New Campaign</Button>
        )}
      </div>

      {/* Campaign Editor (inline, shown when creating) */}
      {showEditor && (
        <CampaignEditor
          onSave={async (data) => {
            await createMutation.mutateAsync(data);
          }}
          onCancel={() => setShowEditor(false)}
          saving={createMutation.isPending}
        />
      )}

      {/* Tabs */}
      {!showEditor && (
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList>
            <TabsTrigger value="campaigns">
              Campaigns
              {campaignsTotal > 0 && (
                <span className="ml-1.5 text-xs text-muted-foreground">({campaignsTotal})</span>
              )}
            </TabsTrigger>
            <TabsTrigger value="pending">
              Pending Review
              {pendingTotal > 0 && (
                <Badge variant="destructive" className="ml-1.5 text-[10px] h-4 min-w-[16px] px-1">
                  {pendingTotal}
                </Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="analytics">Analytics</TabsTrigger>
          </TabsList>

          {/* ===== Campaigns Tab ===== */}
          <TabsContent value="campaigns" className="mt-4">
            {campaignsLoading && (
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-[200px]" />
                ))}
              </div>
            )}

            {!campaignsLoading && campaigns.length === 0 && (
              <div className="flex flex-col items-center justify-center py-16 text-center animate-fade-in">
                <svg
                  className="h-10 w-10 text-muted-foreground/30 mb-4"
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M22 2L11 13" />
                  <path d="M22 2l-7 20-4-9-9-4 20-7z" />
                </svg>
                <p className="text-foreground text-lg font-medium">No campaigns yet</p>
                <p className="text-muted-foreground text-sm mt-1 max-w-sm">
                  Create your first email campaign to start outreach
                </p>
                <Button className="mt-4" onClick={() => setShowEditor(true)}>
                  Create Campaign
                </Button>
              </div>
            )}

            {campaigns.length > 0 && (
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {campaigns.map((campaign) => (
                  <CampaignCard
                    key={campaign.id}
                    campaign={campaign}
                    onView={() => navigate(`/outreach/${campaign.id}`)}
                    onStart={() => handleStartCampaign(campaign.id)}
                    onDelete={() => handleDeleteCampaign(campaign.id)}
                  />
                ))}
              </div>
            )}

            <Pagination
              page={campaignPage}
              pageSize={PAGE_SIZE}
              total={campaignsTotal}
              onPageChange={setCampaignPage}
            />
          </TabsContent>

          {/* ===== Pending Review Tab ===== */}
          <TabsContent value="pending" className="mt-4">
            {pendingLoading && !pendingData && (
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-[240px]" />
                ))}
              </div>
            )}

            {pendingError && (
              <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
                Failed to load outreach items:{" "}
                {pendingError instanceof Error ? pendingError.message : "Unknown error"}
              </div>
            )}

            {!pendingLoading && !pendingError && pendingItems.length === 0 && (
              <div className="flex flex-col items-center justify-center py-16 text-center animate-fade-in">
                <div className="rounded-full bg-success/10 p-4 mb-4">
                  <svg
                    className="h-8 w-8 text-success"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                    <polyline points="22 4 12 14.01 9 11.01" />
                  </svg>
                </div>
                <p className="text-foreground text-lg font-medium">
                  No emails to review
                </p>
                <p className="text-muted-foreground text-sm mt-1 max-w-sm">
                  When the Outreach Agent generates cold emails, they will appear here
                  for your approval before sending.
                </p>
              </div>
            )}

            {pendingItems.length > 0 && (
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {pendingItems.map((item) => (
                  <OutreachCard
                    key={item.id}
                    item={item}
                    onResolve={handleResolve}
                    userRole={user?.role}
                  />
                ))}
              </div>
            )}

            <Pagination
              page={pendingPage}
              pageSize={PAGE_SIZE}
              total={pendingTotal}
              onPageChange={setPendingPage}
            />
          </TabsContent>

          {/* ===== Analytics Tab ===== */}
          <TabsContent value="analytics" className="mt-4">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <MetricCard
                icon={
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M22 2L11 13" />
                    <path d="M22 2l-7 20-4-9-9-4 20-7z" />
                  </svg>
                }
                label="Total Sent"
                value={totalSent}
              />
              <MetricCard
                icon={
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                    <circle cx="12" cy="12" r="3" />
                  </svg>
                }
                label="Opened"
                value={totalOpened}
              />
              <MetricCard
                icon={
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z" />
                  </svg>
                }
                label="Replied"
                value={totalReplied}
              />
              <MetricCard
                icon={
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10" />
                    <line x1="15" y1="9" x2="9" y2="15" />
                    <line x1="9" y1="9" x2="15" y2="15" />
                  </svg>
                }
                label="Bounced"
                value={totalBounced}
              />
            </div>

            {totalSent > 0 && (
              <div className="grid gap-4 md:grid-cols-3 mt-6">
                <Card className="border-border/50">
                  <CardContent className="pt-6">
                    <p className="text-sm text-muted-foreground">Open Rate</p>
                    <p className="text-3xl font-semibold mt-1">
                      {totalSent > 0 ? ((totalOpened / totalSent) * 100).toFixed(1) : 0}%
                    </p>
                  </CardContent>
                </Card>
                <Card className="border-border/50">
                  <CardContent className="pt-6">
                    <p className="text-sm text-muted-foreground">Reply Rate</p>
                    <p className="text-3xl font-semibold mt-1">
                      {totalSent > 0 ? ((totalReplied / totalSent) * 100).toFixed(1) : 0}%
                    </p>
                  </CardContent>
                </Card>
                <Card className="border-border/50">
                  <CardContent className="pt-6">
                    <p className="text-sm text-muted-foreground">Bounce Rate</p>
                    <p className="text-3xl font-semibold mt-1">
                      {totalSent > 0 ? ((totalBounced / totalSent) * 100).toFixed(1) : 0}%
                    </p>
                  </CardContent>
                </Card>
              </div>
            )}

            {/* Per-campaign breakdown */}
            {campaigns.length > 0 && (
              <Card className="border-border/50 mt-6">
                <CardHeader className="pb-3">
                  <CardTitle className="text-base font-medium">Campaign Breakdown</CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-border/50">
                          <th className="text-left py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Campaign</th>
                          <th className="text-right py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Leads</th>
                          <th className="text-right py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Sent</th>
                          <th className="text-right py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Opened</th>
                          <th className="text-right py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Replied</th>
                          <th className="text-right py-3 px-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground/60">Bounced</th>
                        </tr>
                      </thead>
                      <tbody>
                        {campaigns.map((c) => (
                          <tr
                            key={c.id}
                            className="border-b border-border/30 hover:bg-accent/30 transition-colors cursor-pointer"
                            onClick={() => navigate(`/outreach/${c.id}`)}
                          >
                            <td className="py-2.5 px-4 font-medium">{c.name}</td>
                            <td className="py-2.5 px-4 text-right text-muted-foreground">{c.leads_count}</td>
                            <td className="py-2.5 px-4 text-right text-muted-foreground">{c.sent_count}</td>
                            <td className="py-2.5 px-4 text-right text-muted-foreground">{c.opened_count}</td>
                            <td className="py-2.5 px-4 text-right text-muted-foreground">{c.replied_count}</td>
                            <td className="py-2.5 px-4 text-right text-muted-foreground">{c.bounced_count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            )}
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

// --- Campaign Card ---
function CampaignCard({
  campaign,
  onView,
  onStart,
  onDelete,
}: {
  campaign: EmailCampaign;
  onView: () => void;
  onStart: () => void;
  onDelete: () => void;
}) {
  return (
    <Card className="border-border/50 hover:border-primary/30 transition-colors animate-fade-in cursor-pointer" onClick={onView}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-sm font-semibold text-foreground leading-tight truncate">{campaign.name}</h3>
          <Badge variant={campaignStatusVariant(campaign.status)}>{campaign.status}</Badge>
        </div>
        <p className="text-xs text-muted-foreground line-clamp-1 mt-1">{campaign.subject}</p>
      </CardHeader>
      <CardContent className="pb-3">
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          <div>
            <span className="text-muted-foreground">Leads: </span>
            <span className="text-foreground/90">{campaign.leads_count}</span>
          </div>
          <div>
            <span className="text-muted-foreground">Sent: </span>
            <span className="text-foreground/90">{campaign.sent_count}</span>
          </div>
          <div>
            <span className="text-muted-foreground">Opened: </span>
            <span className="text-foreground/90">{campaign.opened_count}</span>
          </div>
          <div>
            <span className="text-muted-foreground">Replied: </span>
            <span className="text-foreground/90">{campaign.replied_count}</span>
          </div>
          {campaign.city_filter && (
            <div className="col-span-2">
              <span className="text-muted-foreground">City: </span>
              <span className="text-foreground/90">{campaign.city_filter}</span>
            </div>
          )}
        </div>
      </CardContent>
      <CardFooter className="gap-2">
        {campaign.status === "draft" && (
          <Button
            variant="success"
            size="sm"
            className="text-xs"
            onClick={(e) => { e.stopPropagation(); onStart(); }}
          >
            Start
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          className="text-xs"
          onClick={(e) => { e.stopPropagation(); onView(); }}
        >
          View
        </Button>
        {campaign.status === "draft" && (
          <Button
            variant="destructive"
            size="sm"
            className="text-xs ml-auto"
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
          >
            Delete
          </Button>
        )}
      </CardFooter>
    </Card>
  );
}

// --- Outreach HITL Card (existing functionality preserved) ---
function OutreachCard({
  item,
  onResolve,
  userRole,
}: {
  item: HITLItem;
  onResolve: (id: string, action: string) => Promise<void>;
  userRole?: string;
}) {
  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const canResolve = !userRole || userRole === "owner" || userRole === "co_owner";

  const handleAction = async (action: string) => {
    setLoadingAction(action);
    try {
      await onResolve(item.id, action);
    } finally {
      setLoadingAction(null);
    }
  };

  const payload = item.payload ?? {};

  return (
    <Card className="relative overflow-hidden border-border/50 hover:border-primary/30 transition-colors animate-fade-in">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <Badge variant="bid">Email</Badge>
          <span className="text-xs text-muted-foreground flex-shrink-0">
            {relativeTime(item.created_at)}
          </span>
        </div>
        <h3 className="text-sm font-semibold text-foreground mt-2 leading-tight">
          {item.title}
        </h3>
      </CardHeader>

      <CardContent className="pb-3">
        {item.description && (
          <p className="text-xs text-muted-foreground mb-3 line-clamp-2">
            {item.description}
          </p>
        )}

        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          {payload.city && (
            <div>
              <span className="text-muted-foreground">City: </span>
              <span className="text-foreground/90">{payload.city}</span>
            </div>
          )}
          {payload.email_count != null && (
            <div>
              <span className="text-muted-foreground">Emails: </span>
              <span className="text-foreground/90">{payload.email_count}</span>
            </div>
          )}
          {payload.campaign_id && (
            <div className="col-span-2">
              <span className="text-muted-foreground">Campaign: </span>
              <span className="text-foreground/90 font-mono text-[10px]">
                {payload.campaign_id}
              </span>
            </div>
          )}
        </div>
      </CardContent>

      <CardFooter className="gap-2 flex-wrap">
        {canResolve ? (
          item.available_actions.map((action) => (
            <Button
              key={action}
              variant={
                action === "approve"
                  ? "success"
                  : action === "reject"
                  ? "destructive"
                  : "outline"
              }
              size="sm"
              disabled={loadingAction !== null}
              onClick={() => handleAction(action)}
              className="text-xs capitalize"
            >
              {loadingAction === action ? (
                <span className="flex items-center gap-1.5">
                  <svg
                    className="h-3 w-3 animate-spin"
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
                  {action}
                </span>
              ) : (
                action
              )}
            </Button>
          ))
        ) : (
          <Badge variant="secondary" className="text-xs">
            Awaiting owner approval
          </Badge>
        )}
      </CardFooter>
    </Card>
  );
}
