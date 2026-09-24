import { useParams, Link } from "@remix-run/react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchDeal, startDealDevelopment, updateDeal } from "~/lib/api";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { useState } from "react";

const STATUS_COLORS: Record<string, string> = {
  negotiation: "bg-yellow-100 text-yellow-800",
  won: "bg-green-100 text-green-800",
  lost: "bg-red-100 text-red-800",
  in_development: "bg-blue-100 text-blue-800",
  delivered: "bg-purple-100 text-purple-800",
};

function formatCurrency(amount: number | null): string {
  if (amount == null) return "--";
  return `$${amount.toLocaleString()}`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "--";
  return new Date(iso).toLocaleString();
}

export default function DealDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const { data: deal, isLoading, error } = useQuery({
    queryKey: ["deal", id],
    queryFn: () => fetchDeal(id!),
    enabled: !!id,
  });

  const startDevMutation = useMutation({
    mutationFn: () => startDealDevelopment(id!),
    onSuccess: (data) => {
      setActionMessage(data.message);
      queryClient.invalidateQueries({ queryKey: ["deal", id] });
    },
    onError: (err: Error) => {
      setActionMessage(`Error: ${err.message}`);
    },
  });

  const markWonMutation = useMutation({
    mutationFn: () => updateDeal(id!, { status: "won" }),
    onSuccess: () => {
      setActionMessage("Deal marked as won.");
      queryClient.invalidateQueries({ queryKey: ["deal", id] });
    },
    onError: (err: Error) => {
      setActionMessage(`Error: ${err.message}`);
    },
  });

  const markLostMutation = useMutation({
    mutationFn: () => updateDeal(id!, { status: "lost" }),
    onSuccess: () => {
      setActionMessage("Deal marked as lost.");
      queryClient.invalidateQueries({ queryKey: ["deal", id] });
    },
    onError: (err: Error) => {
      setActionMessage(`Error: ${err.message}`);
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  if (error || !deal) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-destructive">
          {error ? (error as Error).message : "Deal not found"}
        </CardContent>
      </Card>
    );
  }

  const statusCls = STATUS_COLORS[deal.status] ?? "bg-gray-100 text-gray-800";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link to="/deals" className="hover:text-foreground">
          Deals
        </Link>
        <span>/</span>
        <span className="text-foreground">{deal.title}</span>
      </div>

      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{deal.title}</h1>
          <div className="flex items-center gap-3 mt-1">
            <Badge className={statusCls}>
              {deal.status.replace("_", " ")}
            </Badge>
            {deal.pipeline_a_thread_id && (
              <Badge variant="outline">Pipeline A: {deal.pipeline_a_thread_id}</Badge>
            )}
          </div>
        </div>

        <div className="flex gap-2">
          {deal.status === "negotiation" && (
            <>
              <Button
                variant="default"
                size="sm"
                onClick={() => markWonMutation.mutate()}
                disabled={markWonMutation.isPending}
              >
                Mark Won
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => markLostMutation.mutate()}
                disabled={markLostMutation.isPending}
              >
                Mark Lost
              </Button>
            </>
          )}
          {deal.status === "won" && (
            <Button
              variant="default"
              size="sm"
              onClick={() => startDevMutation.mutate()}
              disabled={startDevMutation.isPending}
            >
              {startDevMutation.isPending ? "Starting..." : "Start Development"}
            </Button>
          )}
        </div>
      </div>

      {actionMessage && (
        <Card>
          <CardContent className="py-3 text-sm">
            {actionMessage}
          </CardContent>
        </Card>
      )}

      {/* Details grid */}
      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Deal Info</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <Row label="Budget" value={formatCurrency(deal.budget)} />
            <Row label="Deadline" value={formatDate(deal.deadline)} />
            <Row label="Created" value={formatDate(deal.created_at)} />
            <Row label="Updated" value={formatDate(deal.updated_at)} />
            {deal.lead_id && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Lead</span>
                <Link
                  to={`/leads/${deal.lead_id}`}
                  className="text-primary hover:underline"
                >
                  View lead
                </Link>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Agreed Scope</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm whitespace-pre-wrap">
              {deal.agreed_scope || "No scope defined yet."}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Client Context */}
      {deal.client_context && Object.keys(deal.client_context).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Client Context</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="text-xs bg-muted p-4 rounded-md overflow-auto max-h-64">
              {JSON.stringify(deal.client_context, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}

      {/* Conversation History */}
      {deal.conversation_history && deal.conversation_history.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Conversation History ({deal.conversation_history.length} messages)
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 max-h-96 overflow-auto">
            {deal.conversation_history.map((msg, i) => (
              <div
                key={i}
                className="border-l-2 border-muted pl-3 py-1 text-sm"
              >
                <span className="font-medium text-xs text-muted-foreground">
                  {(msg as Record<string, any>).role ?? `Message ${i + 1}`}
                </span>
                <p className="mt-0.5">
                  {(msg as Record<string, any>).content ??
                    JSON.stringify(msg)}
                </p>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Design Versions */}
      {deal.design_versions && deal.design_versions.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Design Versions ({deal.design_versions.length})
            </CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="text-xs bg-muted p-4 rounded-md overflow-auto max-h-64">
              {JSON.stringify(deal.design_versions, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}
