import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { Skeleton } from "~/components/ui/skeleton";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { fetchAnalytics } from "~/lib/api";
import type { AnalyticsData } from "~/lib/types";

function MetricCard({
  label,
  value,
  sub,
  subColor,
}: {
  label: string;
  value: string;
  sub?: string;
  subColor?: string;
}) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
      <p className="text-xs text-zinc-500 mb-1">{label}</p>
      <p className="text-2xl font-bold text-white">{value}</p>
      {sub && (
        <p className={`text-xs mt-1 ${subColor ?? "text-zinc-400"}`}>{sub}</p>
      )}
    </div>
  );
}

function triggerCSVDownload(data: AnalyticsData, days: number) {
  const ov = data.overview;
  const rows: string[] = ["category,value"];
  rows.push(`Revenue Total,$${ov.revenue_total}`);
  rows.push(`Jobs Discovered,${ov.jobs_discovered}`);
  rows.push(`Bids Submitted,${ov.bids_submitted}`);
  rows.push(`Deals Won,${ov.deals_won}`);
  if (ov.avg_deal_value != null) rows.push(`Avg Deal Value,$${ov.avg_deal_value}`);
  ov.pipeline_a_funnel.forEach((s) =>
    rows.push(`Pipeline A - ${s.label},${s.count}`)
  );
  ov.pipeline_b_funnel.forEach((s) =>
    rows.push(`Pipeline B - ${s.label},${s.count}`)
  );
  ov.cost_breakdown.forEach((c) =>
    rows.push(`Cost - ${c.category},$${c.amount}`)
  );
  const blob = new Blob([rows.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `analytics-${days}d-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

const ORANGE = "#f97316";
const PURPLE = "#a855f7";
const GRAY = "#71717a";

const AGENT_COLORS: Record<string, string> = {
  bid: ORANGE,
  scout: ORANGE,
  planner: PURPLE,
  critic: PURPLE,
};

export default function AnalyticsPage() {
  const [days, setDays] = useState(30);

  const { data, isLoading, error } = useQuery({
    queryKey: ["analytics", days],
    queryFn: () => fetchAnalytics({ days }),
    staleTime: 5 * 60_000,
  });

  const ov = data?.overview;

  // Recharts-friendly arrays
  const funnelA = (ov?.pipeline_a_funnel ?? []).map((s) => ({
    stage: s.label,
    value: s.count,
  }));
  const funnelB = (ov?.pipeline_b_funnel ?? []).map((s) => ({
    stage: s.label,
    value: s.count,
  }));
  const revenuePlatform = (ov?.revenue_by_platform ?? []).map((r) => ({
    platform: r.platform,
    value: r.revenue,
  }));
  const llmCosts = (ov?.llm_cost_by_agent ?? []).map((r) => ({
    agent: r.agent,
    cost: r.cost_usd,
  }));
  const costs = ov?.cost_breakdown ?? [];
  const costsTotal = costs.reduce((s, c) => s + c.amount, 0);

  const winRate =
    ov && ov.bids_submitted > 0
      ? ((ov.deals_won / ov.bids_submitted) * 100).toFixed(1)
      : "0";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold tracking-tight text-white">
          Analytics
        </h1>
        <div className="flex items-center gap-2">
          <Select
            value={String(days)}
            onValueChange={(v) => setDays(Number(v))}
          >
            <SelectTrigger className="w-36 bg-zinc-900 border-zinc-700">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7">Last 7 days</SelectItem>
              <SelectItem value="14">Last 14 days</SelectItem>
              <SelectItem value="30">Last 30 days</SelectItem>
              <SelectItem value="90">Last 90 days</SelectItem>
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            className="border-zinc-700"
            disabled={!data}
            onClick={() => data && triggerCSVDownload(data, days)}
          >
            Export
          </Button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load analytics:{" "}
          {error instanceof Error ? error.message : "Unknown error"}
        </div>
      )}

      {/* Row 1 — Metric Cards */}
      <div className="grid grid-cols-3 gap-4">
        {isLoading ? (
          Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))
        ) : (
          <>
            <MetricCard
              label="Revenue"
              value={`$${(ov?.revenue_total ?? 0).toLocaleString()}`}
              sub={`${ov?.deals_won ?? 0} deals won`}
              subColor="text-green-400"
            />
            <MetricCard
              label="Win Rate"
              value={`${winRate}%`}
              sub={`${ov?.bids_submitted ?? 0} bids submitted`}
            />
            <MetricCard
              label="Avg Deal Value"
              value={
                ov?.avg_deal_value != null
                  ? `$${ov.avg_deal_value.toLocaleString()}`
                  : "--"
              }
              sub={`${ov?.jobs_discovered ?? 0} jobs discovered`}
            />
          </>
        )}
      </div>

      {/* Row 2 — Funnels */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
          <p className="text-sm font-semibold text-white mb-4">
            Pipeline A — Bids Funnel
          </p>
          {isLoading ? (
            <Skeleton className="h-48" />
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart
                data={funnelA}
                layout="vertical"
                margin={{ left: 16, right: 16, top: 0, bottom: 0 }}
              >
                <XAxis type="number" hide />
                <YAxis
                  type="category"
                  dataKey="stage"
                  width={110}
                  tick={{ fill: "#a1a1aa", fontSize: 12 }}
                />
                <Tooltip
                  contentStyle={{
                    background: "#18181b",
                    border: "1px solid #3f3f46",
                    borderRadius: 6,
                  }}
                  labelStyle={{ color: "#fff" }}
                  itemStyle={{ color: ORANGE }}
                />
                <Bar dataKey="value" fill={ORANGE} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
          <p className="text-sm font-semibold text-white mb-4">
            Pipeline B — Outreach Funnel
          </p>
          {isLoading ? (
            <Skeleton className="h-48" />
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart
                data={funnelB}
                layout="vertical"
                margin={{ left: 16, right: 16, top: 0, bottom: 0 }}
              >
                <XAxis type="number" hide />
                <YAxis
                  type="category"
                  dataKey="stage"
                  width={110}
                  tick={{ fill: "#a1a1aa", fontSize: 12 }}
                />
                <Tooltip
                  contentStyle={{
                    background: "#18181b",
                    border: "1px solid #3f3f46",
                    borderRadius: 6,
                  }}
                  labelStyle={{ color: "#fff" }}
                  itemStyle={{ color: ORANGE }}
                />
                <Bar dataKey="value" fill={ORANGE} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Row 3 — Costs Breakdown */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
        <div className="flex items-center justify-between mb-4">
          <p className="text-sm font-semibold text-white">Costs Breakdown</p>
          <div className="flex items-center gap-2">
            <span className="text-xs text-zinc-500">Monthly</span>
            <Badge className="bg-purple-500/20 text-purple-400 text-xs">
              PRO ${costsTotal.toLocaleString()}
            </Badge>
          </div>
        </div>
        {isLoading ? (
          <Skeleton className="h-32" />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-zinc-500 text-xs border-b border-zinc-800">
                <th className="text-left pb-2 font-medium">Category</th>
                <th className="text-right pb-2 font-medium">Monthly ($)</th>
                <th className="text-right pb-2 font-medium">%</th>
              </tr>
            </thead>
            <tbody>
              {costs.map((row) => (
                <tr
                  key={row.category}
                  className="border-b border-zinc-800/50 last:border-0"
                >
                  <td className="py-2 text-zinc-300">{row.category}</td>
                  <td className="py-2 text-right text-white">
                    ${row.amount.toLocaleString()}
                  </td>
                  <td className="py-2 text-right text-zinc-400">
                    {costsTotal > 0
                      ? ((row.amount / costsTotal) * 100).toFixed(1)
                      : "0"}
                    %
                  </td>
                </tr>
              ))}
              <tr className="font-semibold">
                <td className="pt-3 text-white">Total</td>
                <td className="pt-3 text-right text-white">
                  ${costsTotal.toLocaleString()}
                </td>
                <td className="pt-3 text-right text-zinc-400">100%</td>
              </tr>
            </tbody>
          </table>
        )}
      </div>

      {/* Row 4 — Revenue by Platform + LLM Cost by Agent */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
          <p className="text-sm font-semibold text-white mb-4">
            Revenue by Platform
          </p>
          {isLoading ? (
            <Skeleton className="h-48" />
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart
                data={revenuePlatform}
                margin={{ left: 0, right: 0, top: 0, bottom: 0 }}
              >
                <XAxis
                  dataKey="platform"
                  tick={{ fill: "#a1a1aa", fontSize: 12 }}
                />
                <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} />
                <Tooltip
                  contentStyle={{
                    background: "#18181b",
                    border: "1px solid #3f3f46",
                    borderRadius: 6,
                  }}
                  labelStyle={{ color: "#fff" }}
                  formatter={(v: number | undefined) => [`$${v ?? 0}`, "Revenue"]}
                />
                <Bar dataKey="value" fill={ORANGE} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
          <p className="text-sm font-semibold text-white mb-4">
            LLM Cost by Agent
          </p>
          {isLoading ? (
            <Skeleton className="h-48" />
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart
                data={llmCosts}
                layout="vertical"
                margin={{ left: 16, right: 16, top: 0, bottom: 0 }}
              >
                <XAxis type="number" hide />
                <YAxis
                  type="category"
                  dataKey="agent"
                  width={72}
                  tick={{ fill: "#a1a1aa", fontSize: 12 }}
                />
                <Tooltip
                  contentStyle={{
                    background: "#18181b",
                    border: "1px solid #3f3f46",
                    borderRadius: 6,
                  }}
                  labelStyle={{ color: "#fff" }}
                  formatter={(v: number | undefined) => [`$${(v ?? 0).toFixed(2)}`, "Cost"]}
                />
                <Bar dataKey="cost" radius={[0, 4, 4, 0]}>
                  {llmCosts.map((entry) => (
                    <Cell
                      key={entry.agent}
                      fill={AGENT_COLORS[entry.agent.toLowerCase()] ?? GRAY}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  );
}
