import { useState } from "react";
import { useNavigate } from "@remix-run/react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader } from "~/components/ui/card";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "~/components/ui/tabs";
import { Pagination } from "~/components/pagination";
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

function campaignStatusClass(status: string): string {
  switch (status) {
    case "active":
    case "sending":
      return "bg-green-500/20 text-green-400";
    case "completed":
      return "bg-blue-500/20 text-blue-400";
    case "draft":
      return "bg-zinc-700 text-zinc-400";
    case "paused":
      return "bg-yellow-500/20 text-yellow-400";
    case "failed":
      return "bg-red-500/20 text-red-400";
    default:
      return "bg-zinc-700 text-zinc-400";
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

  const { data: campaignsData, isLoading: campaignsLoading } = useQuery({
    queryKey: ["campaigns", campaignPage],
    queryFn: () => fetchCampaigns({ limit: PAGE_SIZE, offset: campaignPage * PAGE_SIZE }),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

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
        description: err instanceof Error ? err.message : "Something went wrong",
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

  const totalSent = campaigns.reduce((s, c) => s + c.sent_count, 0);
  const totalOpened = campaigns.reduce((s, c) => s + c.opened_count, 0);
  const totalReplied = campaigns.reduce((s, c) => s + c.replied_count, 0);
  const totalBounced = campaigns.reduce((s, c) => s + c.bounced_count, 0);
  const openRate = totalSent > 0 ? ((totalOpened / totalSent) * 100).toFixed(1) : "0";
  const replyRate = totalSent > 0 ? ((totalReplied / totalSent) * 100).toFixed(1) : "0";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-white">Outreach</h1>
          {pendingTotal > 0 && (
            <Badge className="bg-orange-500 text-white text-xs">{pendingTotal} pending</Badge>
          )}
        </div>
        {!showEditor && (
          <Button
            onClick={() => setShowEditor(true)}
            className="bg-orange-500 hover:bg-orange-600 text-white"
            size="sm"
          >
            New Campaign
          </Button>
        )}
      </div>

      {/* Always-visible stat cards */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
          <p className="text-xs text-zinc-500 mb-1">Total Sent</p>
          <p className="text-2xl font-bold text-white">{totalSent}</p>
          <p className="text-xs text-zinc-500 mt-1">{campaignsTotal} campaigns</p>
        </div>
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
          <p className="text-xs text-zinc-500 mb-1">Opened</p>
          <p className="text-2xl font-bold text-white">{totalOpened}</p>
          <p className="text-xs text-zinc-500 mt-1">{openRate}% open rate</p>
        </div>
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
          <p className="text-xs text-zinc-500 mb-1">Replied</p>
          <p className="text-2xl font-bold text-white">{totalReplied}</p>
          <p className="text-xs text-zinc-500 mt-1">{replyRate}% reply rate</p>
        </div>
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
          <p className="text-xs text-zinc-500 mb-1">Pending Review</p>
          <p className="text-2xl font-bold text-white">{pendingTotal}</p>
          <p className="text-xs text-zinc-500 mt-1">awaiting approval</p>
        </div>
      </div>

      {/* Campaign Editor */}
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
          <TabsList className="bg-zinc-900 border border-zinc-800">
            <TabsTrigger value="campaigns">
              Campaigns
              {campaignsTotal > 0 && (
                <span className="ml-1.5 text-xs text-zinc-500">({campaignsTotal})</span>
              )}
            </TabsTrigger>
            <TabsTrigger value="pending">
              Pending Review
              {pendingTotal > 0 && (
                <Badge className="ml-1.5 bg-orange-500 text-white text-[10px] h-4 min-w-[16px] px-1">
                  {pendingTotal}
                </Badge>
              )}
            </TabsTrigger>
          </TabsList>

          {/* ===== Campaigns Tab — table rows ===== */}
          <TabsContent value="campaigns" className="mt-4">
            {campaignsLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-14" />
                ))}
              </div>
            ) : campaigns.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <p className="text-zinc-400 text-base font-medium">No campaigns yet</p>
                <p className="text-zinc-600 text-sm mt-1">Create your first campaign to start outreach</p>
                <Button
                  className="mt-4 bg-orange-500 hover:bg-orange-600 text-white"
                  size="sm"
                  onClick={() => setShowEditor(true)}
                >
                  Create Campaign
                </Button>
              </div>
            ) : (
              <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-zinc-800 text-zinc-500 text-xs">
                      <th className="text-left px-5 py-3 font-medium">Campaign</th>
                      <th className="text-left px-4 py-3 font-medium">Status</th>
                      <th className="text-right px-4 py-3 font-medium">Leads</th>
                      <th className="text-right px-4 py-3 font-medium">Sent</th>
                      <th className="text-right px-4 py-3 font-medium">Opened</th>
                      <th className="text-right px-4 py-3 font-medium">Replied</th>
                      <th className="px-4 py-3" />
                    </tr>
                  </thead>
                  <tbody>
                    {campaigns.map((campaign) => (
                      <tr
                        key={campaign.id}
                        className="border-b border-zinc-800/50 last:border-0 hover:bg-zinc-800/40 transition-colors cursor-pointer"
                        onClick={() => navigate(`/outreach/${campaign.id}`)}
                      >
                        <td className="px-5 py-3">
                          <p className="font-semibold text-white">{campaign.name}</p>
                          <p className="text-xs text-zinc-500 truncate max-w-[200px]">{campaign.subject}</p>
                        </td>
                        <td className="px-4 py-3">
                          <span className={`text-[10px] font-medium px-2 py-0.5 rounded ${campaignStatusClass(campaign.status)}`}>
                            {campaign.status}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right text-zinc-400">{campaign.leads_count}</td>
                        <td className="px-4 py-3 text-right text-zinc-400">{campaign.sent_count}</td>
                        <td className="px-4 py-3 text-right text-zinc-400">{campaign.opened_count}</td>
                        <td className="px-4 py-3 text-right text-zinc-400">{campaign.replied_count}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-1 justify-end" onClick={(e) => e.stopPropagation()}>
                            {campaign.status === "draft" && (
                              <Button
                                size="sm"
                                className="h-7 text-xs bg-orange-500 hover:bg-orange-600 text-white"
                                onClick={() => handleStartCampaign(campaign.id)}
                              >
                                Start
                              </Button>
                            )}
                            {campaign.status === "draft" && (
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-7 text-xs border-zinc-700 text-red-400 hover:text-red-300"
                                onClick={() => handleDeleteCampaign(campaign.id)}
                              >
                                Delete
                              </Button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
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
            {pendingLoading && !pendingData ? (
              <div className="space-y-2">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-32" />
                ))}
              </div>
            ) : pendingError ? (
              <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
                Failed to load items: {pendingError instanceof Error ? pendingError.message : "Unknown error"}
              </div>
            ) : pendingItems.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <p className="text-zinc-400 text-base font-medium">No emails to review</p>
                <p className="text-zinc-600 text-sm mt-1">
                  When the Outreach Agent generates cold emails, they appear here for approval.
                </p>
              </div>
            ) : (
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
        </Tabs>
      )}
    </div>
  );
}

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
  const payload = item.payload ?? {};

  const handleAction = async (action: string) => {
    setLoadingAction(action);
    try {
      await onResolve(item.id, action);
    } finally {
      setLoadingAction(null);
    }
  };

  return (
    <Card className="bg-zinc-900 border border-zinc-800 hover:border-zinc-700 transition-colors">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <Badge className="bg-orange-500/20 text-orange-400 text-[10px]">Email</Badge>
          <span className="text-xs text-zinc-500">{relativeTime(item.created_at)}</span>
        </div>
        <p className="text-sm font-semibold text-white mt-2 leading-tight">{item.title}</p>
      </CardHeader>
      <CardContent className="pb-3">
        {item.description && (
          <p className="text-xs text-zinc-500 mb-3 line-clamp-2">{item.description}</p>
        )}
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          {payload.city && (
            <div>
              <span className="text-zinc-500">City: </span>
              <span className="text-zinc-300">{payload.city}</span>
            </div>
          )}
          {payload.email_count != null && (
            <div>
              <span className="text-zinc-500">Emails: </span>
              <span className="text-zinc-300">{payload.email_count}</span>
            </div>
          )}
        </div>
      </CardContent>
      <CardFooter className="gap-2 flex-wrap">
        {canResolve ? (
          item.available_actions.map((action) => (
            <Button
              key={action}
              size="sm"
              disabled={loadingAction !== null}
              onClick={() => handleAction(action)}
              className={`text-xs capitalize h-7 ${
                action === "approve"
                  ? "bg-green-600 hover:bg-green-700 text-white"
                  : action === "reject"
                  ? "bg-red-600 hover:bg-red-700 text-white"
                  : "border border-zinc-700 bg-transparent text-zinc-300 hover:bg-zinc-800"
              }`}
            >
              {loadingAction === action ? `${action}...` : action}
            </Button>
          ))
        ) : (
          <Badge className="bg-zinc-800 text-zinc-400 text-xs">Awaiting owner approval</Badge>
        )}
      </CardFooter>
    </Card>
  );
}
