import { useState, useRef, useEffect, useCallback } from "react";
import { useParams, Link } from "@remix-run/react";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Separator } from "~/components/ui/separator";
import { Skeleton } from "~/components/ui/skeleton";
import { Textarea } from "~/components/ui/textarea";
import { Avatar, AvatarFallback } from "~/components/ui/avatar";
import {
  fetchBid,
  fetchNegotiation,
  fetchNegotiationMessages,
  sendNegotiationMessage,
  takeOverNegotiation,
  releaseNegotiation,
} from "~/lib/api";
import type { NegotiationStage, NegotiationMessage } from "~/lib/types";
import { relativeTime } from "~/lib/utils";
import { toast } from "~/hooks/use-toast";
import { useWsSubscription } from "~/hooks/use-ws-subscription";

/* ---------- helpers ---------- */

function formatCurrency(v: number | null): string {
  if (v == null) return "--";
  return `$${v.toLocaleString()}`;
}

/* ---------- negotiation stage config ---------- */

const STAGE_COLORS: Record<NegotiationStage, string> = {
  initial: "bg-zinc-600 text-zinc-200",
  qualifying: "bg-blue-500/20 text-blue-400",
  proposing: "bg-purple-500/20 text-purple-400",
  negotiating: "bg-orange-500/20 text-orange-400",
  closing: "bg-green-500/20 text-green-400",
  won: "bg-emerald-500/20 text-emerald-400",
  lost: "bg-red-500/20 text-red-400",
};

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-zinc-600 text-zinc-200",
  submitted: "bg-blue-500/20 text-blue-400",
  accepted: "bg-green-500/20 text-green-400",
  rejected: "bg-red-500/20 text-red-400",
  withdrawn: "bg-yellow-500/20 text-yellow-400",
  active_dialog: "bg-orange-500/20 text-orange-400",
};

/* ---------- sender config ---------- */

const SENDER_COLORS: Record<NegotiationMessage["sender"], string> = {
  ai: "text-blue-400",
  client: "text-green-400",
  operator: "text-orange-400",
};

const SENDER_BG: Record<NegotiationMessage["sender"], string> = {
  ai: "bg-blue-500/10 border-blue-500/20",
  client: "bg-green-500/10 border-green-500/20",
  operator: "bg-orange-500/10 border-orange-500/20",
};

const SENDER_AVATAR_BG: Record<NegotiationMessage["sender"], string> = {
  ai: "bg-blue-500/20 text-blue-400",
  client: "bg-green-500/20 text-green-400",
  operator: "bg-orange-500/20 text-orange-400",
};

const SENDER_INITIALS: Record<NegotiationMessage["sender"], string> = {
  ai: "AI",
  client: "CL",
  operator: "OP",
};

const TYPE_BADGE_COLORS: Record<string, string> = {
  message: "bg-zinc-700 text-zinc-300",
  proposal: "bg-purple-500/20 text-purple-400",
  counter_offer: "bg-amber-500/20 text-amber-400",
  system: "bg-zinc-800 text-zinc-500",
};

/* ---------- message bubble ---------- */

function MessageBubble({ message }: { message: NegotiationMessage }) {
  const isOutbound = message.sender === "ai" || message.sender === "operator";

  return (
    <div
      className={`flex gap-3 ${isOutbound ? "flex-row-reverse" : "flex-row"}`}
    >
      <Avatar className="h-8 w-8 shrink-0">
        <AvatarFallback
          className={`text-[10px] font-bold ${SENDER_AVATAR_BG[message.sender]}`}
        >
          {SENDER_INITIALS[message.sender]}
        </AvatarFallback>
      </Avatar>
      <div
        className={`max-w-[75%] space-y-1 ${
          isOutbound ? "items-end" : "items-start"
        }`}
      >
        <div className={`flex items-center gap-2 ${isOutbound ? "flex-row-reverse" : ""}`}>
          <span
            className={`text-[10px] font-medium capitalize ${SENDER_COLORS[message.sender]}`}
          >
            {message.sender}
          </span>
          {message.type !== "message" && (
            <Badge className={`text-[9px] ${TYPE_BADGE_COLORS[message.type] ?? TYPE_BADGE_COLORS.message}`}>
              {message.type.replace("_", " ")}
            </Badge>
          )}
          <span className="text-[10px] text-zinc-600">
            {relativeTime(message.timestamp)}
          </span>
        </div>
        <div
          className={`rounded-lg border px-3 py-2 text-sm text-zinc-200 ${SENDER_BG[message.sender]}`}
        >
          {message.content}
        </div>
      </div>
    </div>
  );
}

/* ---------- page ---------- */

export default function BidDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [messageText, setMessageText] = useState("");

  useWsSubscription("subscribe:channel", id ? `bid:${id}` : undefined);

  // Fetch bid detail
  const {
    data: bid,
    isLoading: bidLoading,
    error: bidError,
  } = useQuery({
    queryKey: ["bid", id],
    queryFn: () => fetchBid(id!),
    enabled: !!id,
  });

  // Fetch negotiation state
  const { data: negotiation } = useQuery({
    queryKey: ["negotiation", id],
    queryFn: () => fetchNegotiation(id!),
    enabled: !!id,
    retry: 1,
  });

  // Fetch messages
  const { data: messagesData, isLoading: messagesLoading } = useQuery({
    queryKey: ["negotiation-messages", id],
    queryFn: () => fetchNegotiationMessages(id!),
    enabled: !!id,
    refetchInterval: 10_000,
    retry: 1,
  });

  const messages = messagesData?.messages ?? [];

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  // Send message mutation
  const sendMutation = useMutation({
    mutationFn: (content: string) => sendNegotiationMessage(id!, content),
    onSuccess: () => {
      setMessageText("");
      queryClient.invalidateQueries({ queryKey: ["negotiation-messages", id] });
      queryClient.invalidateQueries({ queryKey: ["negotiation", id] });
    },
    onError: (err: Error) => {
      toast({
        title: "Failed to send message",
        description: err.message,
        variant: "destructive",
      });
    },
  });

  // Take over mutation
  const takeOverMutation = useMutation({
    mutationFn: () => takeOverNegotiation(id!),
    onSuccess: (data) => {
      toast({ title: "Took over negotiation", description: data.message });
      queryClient.invalidateQueries({ queryKey: ["bid", id] });
      queryClient.invalidateQueries({ queryKey: ["negotiation", id] });
    },
    onError: (err: Error) => {
      toast({
        title: "Failed to take over",
        description: err.message,
        variant: "destructive",
      });
    },
  });

  // Release mutation
  const releaseMutation = useMutation({
    mutationFn: () => releaseNegotiation(id!),
    onSuccess: (data) => {
      toast({ title: "Released negotiation", description: data.message });
      queryClient.invalidateQueries({ queryKey: ["bid", id] });
      queryClient.invalidateQueries({ queryKey: ["negotiation", id] });
    },
    onError: (err: Error) => {
      toast({
        title: "Failed to release",
        description: err.message,
        variant: "destructive",
      });
    },
  });

  const handleSend = useCallback(() => {
    if (!messageText.trim() || !id) return;
    sendMutation.mutate(messageText.trim());
  }, [messageText, id, sendMutation]);

  const isOperatorOverride = bid?.operator_override ?? negotiation?.operator_override ?? false;
  const stage: NegotiationStage = bid?.negotiation_stage ?? negotiation?.stage ?? "initial";

  /* ---------- loading ---------- */

  if (bidLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-5 w-32" />
        <div className="flex items-start justify-between gap-4">
          <Skeleton className="h-8 w-80" />
          <Skeleton className="h-9 w-28" />
        </div>
        <Skeleton className="h-10 w-64" />
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <Skeleton className="h-[400px]" />
          </div>
          <div className="space-y-4">
            <Skeleton className="h-[180px]" />
            <Skeleton className="h-[120px]" />
          </div>
        </div>
      </div>
    );
  }

  /* ---------- error ---------- */

  if (bidError || !bid) {
    return (
      <div className="space-y-4">
        <Link to="/bids" className="text-sm text-orange-400 hover:underline">
          Back to Bids
        </Link>
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {bidError instanceof Error ? bidError.message : "Bid not found"}
        </div>
      </div>
    );
  }

  const statusCls = STATUS_COLORS[bid.status] ?? "bg-zinc-700 text-zinc-300";
  const stageCls = STAGE_COLORS[stage] ?? STAGE_COLORS.initial;

  return (
    <div className="space-y-5">
      {/* Breadcrumb */}
      <Link
        to="/bids"
        className="text-sm text-orange-400 hover:underline inline-flex items-center gap-1"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className="h-3 w-3"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="m15 18-6-6 6-6" />
        </svg>
        Back to Bids
      </Link>

      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold tracking-tight text-white leading-tight">
            {bid.job_title ?? `Bid #${bid.id.slice(0, 8)}`}
          </h1>
          <div className="flex items-center gap-3 mt-2 text-sm flex-wrap">
            <span className="text-orange-400 font-semibold text-lg">
              {formatCurrency(bid.bid_amount)}
            </span>
            <Badge className={`text-[10px] capitalize ${statusCls}`}>
              {bid.status.replace(/_/g, " ")}
            </Badge>
            <Badge variant="outline" className="border-zinc-700 text-zinc-300">
              {bid.platform}
            </Badge>
            {bid.job_id && (
              <Link
                to={`/jobs/${bid.job_id}`}
                className="text-xs text-zinc-400 hover:text-orange-400 transition-colors"
              >
                View Job
              </Link>
            )}
          </div>
        </div>

        {/* Action bar */}
        <div className="flex items-center gap-2 shrink-0">
          {!isOperatorOverride ? (
            <Button
              variant="outline"
              size="sm"
              className="border-orange-500/50 text-orange-400 hover:bg-orange-500/10"
              onClick={() => takeOverMutation.mutate()}
              disabled={takeOverMutation.isPending}
            >
              {takeOverMutation.isPending ? "Taking over..." : "Take Over"}
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              className="border-zinc-700 text-zinc-300 hover:bg-zinc-800"
              onClick={() => releaseMutation.mutate()}
              disabled={releaseMutation.isPending}
            >
              {releaseMutation.isPending ? "Releasing..." : "Release to AI"}
            </Button>
          )}
        </div>
      </div>

      {/* Negotiation stage indicator */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
        <div className="flex items-center gap-3">
          <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium">
            Negotiation Stage
          </p>
          <Badge className={`capitalize ${stageCls}`}>{stage}</Badge>
          {isOperatorOverride && (
            <Badge className="bg-orange-500/20 text-orange-400 text-[10px]">
              Operator Control
            </Badge>
          )}
        </div>
        {/* Stage progress dots */}
        <div className="flex items-center gap-1 mt-3">
          {(
            ["initial", "qualifying", "proposing", "negotiating", "closing"] as NegotiationStage[]
          ).map((s, idx) => {
            const stages: NegotiationStage[] = [
              "initial",
              "qualifying",
              "proposing",
              "negotiating",
              "closing",
            ];
            const currentIdx = stages.indexOf(stage);
            const isWonLost = stage === "won" || stage === "lost";
            const isDone = isWonLost || idx < currentIdx;
            const isActive = !isWonLost && idx === currentIdx;

            return (
              <div key={s} className="flex items-center">
                <div
                  className={`h-2.5 w-2.5 rounded-full ${
                    isDone
                      ? "bg-green-500"
                      : isActive
                      ? "bg-orange-500"
                      : "bg-zinc-700"
                  }`}
                />
                {idx < 4 && (
                  <div
                    className={`h-0.5 w-8 ${
                      isDone ? "bg-green-500" : "bg-zinc-700"
                    }`}
                  />
                )}
              </div>
            );
          })}
          {(stage === "won" || stage === "lost") && (
            <>
              <div className={`h-0.5 w-8 ${stage === "won" ? "bg-green-500" : "bg-red-500"}`} />
              <div
                className={`h-2.5 w-2.5 rounded-full ${
                  stage === "won" ? "bg-emerald-500" : "bg-red-500"
                }`}
              />
            </>
          )}
        </div>
      </div>

      {/* Main layout: chat + sidebar */}
      <div className="grid gap-5 lg:grid-cols-3">
        {/* Chat interface */}
        <div className="lg:col-span-2 flex flex-col bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
          {/* Chat header */}
          <div className="px-5 py-3 border-b border-zinc-800 flex items-center justify-between">
            <p className="text-sm font-medium text-white">
              Messages
              {messages.length > 0 && (
                <span className="text-zinc-500 ml-2 text-xs">
                  ({messages.length})
                </span>
              )}
            </p>
            {messagesLoading && (
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-zinc-600 border-t-orange-500" />
            )}
          </div>

          {/* Message list */}
          <div className="flex-1 overflow-y-auto p-5 space-y-4 min-h-[300px] max-h-[500px]">
            {messages.length === 0 ? (
              <div className="flex items-center justify-center h-full">
                <div className="text-center space-y-2">
                  <p className="text-zinc-500 text-sm">No messages yet</p>
                  <p className="text-zinc-600 text-xs">
                    Messages will appear here when the negotiation begins
                  </p>
                </div>
              </div>
            ) : (
              messages.map((msg) => (
                <MessageBubble key={msg.id} message={msg} />
              ))
            )}
            <div ref={messagesEndRef} />
          </div>

          <Separator className="bg-zinc-800" />

          {/* Message input */}
          <div className="p-4 space-y-3">
            <Textarea
              placeholder="Type a message..."
              value={messageText}
              onChange={(e) => setMessageText(e.target.value)}
              className="bg-zinc-950 border-zinc-700 text-white resize-none min-h-[60px]"
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
            />
            <div className="flex items-center justify-end">
              <Button
                size="sm"
                className="bg-orange-500 hover:bg-orange-600 text-white"
                disabled={!messageText.trim() || sendMutation.isPending}
                onClick={handleSend}
              >
                {sendMutation.isPending ? "Sending..." : "Send"}
              </Button>
            </div>
          </div>
        </div>

        {/* Sidebar */}
        <div className="space-y-4">
          {/* Proposal */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-3">
              Proposal
            </p>
            {bid.proposal_text ? (
              <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
                {bid.proposal_text}
              </p>
            ) : bid.cover_letter ? (
              <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
                {bid.cover_letter}
              </p>
            ) : (
              <p className="text-sm text-zinc-600 italic">
                No proposal text available.
              </p>
            )}
          </div>

          {/* Bid details */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-3">
              Bid Details
            </p>
            <div className="space-y-2.5 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Amount</span>
                <span className="text-orange-400 font-semibold">
                  {formatCurrency(bid.bid_amount)}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Delivery</span>
                <span className="text-white">
                  {bid.delivery_days != null
                    ? `${bid.delivery_days} days`
                    : "Not specified"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Confidence</span>
                <span>
                  {bid.confidence_score != null ? (
                    <span
                      className={
                        bid.confidence_score >= 0.7
                          ? "text-green-400"
                          : bid.confidence_score >= 0.4
                          ? "text-amber-400"
                          : "text-red-400"
                      }
                    >
                      {Math.round(bid.confidence_score * 100)}%
                    </span>
                  ) : (
                    <span className="text-zinc-600">N/A</span>
                  )}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Platform</span>
                <Badge
                  variant="outline"
                  className="border-zinc-700 text-zinc-300 text-xs"
                >
                  {bid.platform}
                </Badge>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Status</span>
                <Badge className={`text-[10px] capitalize ${statusCls}`}>
                  {bid.status.replace(/_/g, " ")}
                </Badge>
              </div>
              {bid.submitted_at && (
                <div className="flex items-center justify-between">
                  <span className="text-zinc-500">Submitted</span>
                  <span className="text-white text-xs">
                    {relativeTime(bid.submitted_at)}
                  </span>
                </div>
              )}
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Created</span>
                <span className="text-white text-xs">
                  {relativeTime(bid.created_at)}
                </span>
              </div>
            </div>
          </div>

          {/* Quick actions */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-3">
              Actions
            </p>
            <div className="space-y-2">
              {bid.job_id && (
                <Button
                  variant="outline"
                  className="w-full border-zinc-700"
                  size="sm"
                  asChild
                >
                  <Link to={`/jobs/${bid.job_id}`}>View Job</Link>
                </Button>
              )}
              {!isOperatorOverride ? (
                <Button
                  className="w-full border-orange-500/50 text-orange-400 hover:bg-orange-500/10"
                  variant="outline"
                  size="sm"
                  onClick={() => takeOverMutation.mutate()}
                  disabled={takeOverMutation.isPending}
                >
                  {takeOverMutation.isPending ? "Taking over..." : "Take Over Negotiation"}
                </Button>
              ) : (
                <Button
                  variant="ghost"
                  className="w-full text-zinc-400 hover:text-white"
                  size="sm"
                  onClick={() => releaseMutation.mutate()}
                  disabled={releaseMutation.isPending}
                >
                  {releaseMutation.isPending ? "Releasing..." : "Release to AI"}
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
