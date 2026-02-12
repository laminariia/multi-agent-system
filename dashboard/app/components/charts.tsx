import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  AreaChart,
  Area,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";

const COLORS = [
  "hsl(210, 100%, 56%)",  // blue
  "hsl(160, 84%, 39%)",   // green
  "hsl(38, 92%, 50%)",    // amber
  "hsl(340, 82%, 52%)",   // rose
  "hsl(262, 83%, 58%)",   // purple
  "hsl(190, 95%, 39%)",   // cyan
];

const tooltipStyle = {
  contentStyle: {
    background: "hsl(var(--card))",
    border: "1px solid hsl(var(--border))",
    borderRadius: "8px",
    fontSize: "12px",
    color: "hsl(var(--foreground))",
  },
  itemStyle: { color: "hsl(var(--foreground))" },
};

// --- Jobs by Platform bar chart ---

interface JobsByPlatformData {
  platform: string;
  count: number;
}

export function JobsByPlatformChart({ data, title = "Jobs by Platform" }: { data: JobsByPlatformData[]; title?: string }) {
  if (!data.length) return null;

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[200px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="platform" tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} allowDecimals={false} />
              <Tooltip {...tooltipStyle} />
              <Bar dataKey="count" fill="hsl(210, 100%, 56%)" radius={[4, 4, 0, 0]} maxBarSize={40} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

// --- HITL by Type pie chart ---

interface HITLByTypeData {
  type: string;
  pending: number;
  resolved: number;
}

export function HITLByTypeChart({ data }: { data: HITLByTypeData[] }) {
  const pieData = data.map((d) => ({
    name: d.type,
    value: d.pending + d.resolved,
  })).filter((d) => d.value > 0);

  if (!pieData.length) return null;

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">HITL by Type</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[200px]">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={pieData}
                cx="50%"
                cy="50%"
                innerRadius={50}
                outerRadius={80}
                paddingAngle={2}
                dataKey="value"
                label={({ name, value }: any) => `${name ?? ""}: ${value ?? ""}`}
                labelLine={false}
              >
                {pieData.map((_, index) => (
                  <Cell key={index} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip {...tooltipStyle} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

// --- Agent Status donut ---

interface AgentStatusData {
  status: string;
  count: number;
}

const statusColors: Record<string, string> = {
  working: "hsl(160, 84%, 39%)",
  idle: "hsl(210, 100%, 56%)",
  paused: "hsl(38, 92%, 50%)",
  error: "hsl(340, 82%, 52%)",
  dead: "hsl(0, 0%, 40%)",
};

export function AgentStatusChart({ data }: { data: AgentStatusData[] }) {
  const filtered = data.filter((d) => d.count > 0);
  if (!filtered.length) return null;

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Agent Status</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[200px]">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={filtered}
                cx="50%"
                cy="50%"
                innerRadius={50}
                outerRadius={80}
                paddingAngle={2}
                dataKey="count"
                nameKey="status"
                label={({ name, value }: any) => `${name ?? ""}: ${value ?? ""}`}
                labelLine={false}
              >
                {filtered.map((entry) => (
                  <Cell key={entry.status} fill={statusColors[entry.status] ?? COLORS[0]} />
                ))}
              </Pie>
              <Tooltip {...tooltipStyle} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

// --- Jobs over time area chart ---

interface JobsOverTimeData {
  date: string;
  discovered: number;
  completed: number;
}

export function JobsOverTimeChart({ data }: { data: JobsOverTimeData[] }) {
  if (!data.length) return null;

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Jobs Over Time</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[200px]">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="date" tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} allowDecimals={false} />
              <Tooltip {...tooltipStyle} />
              <Area type="monotone" dataKey="discovered" stroke="hsl(210, 100%, 56%)" fill="hsl(210, 100%, 56%)" fillOpacity={0.15} strokeWidth={2} />
              <Area type="monotone" dataKey="completed" stroke="hsl(160, 84%, 39%)" fill="hsl(160, 84%, 39%)" fillOpacity={0.15} strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

// --- HITL Trends area chart ---

interface HITLTrendData {
  date: string;
  created: number;
  resolved: number;
}

export function HITLTrendsChart({ data }: { data: HITLTrendData[] }) {
  if (!data.length) return null;

  return (
    <Card className="border-border/50">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">HITL Trends (7 days)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[200px]">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v: string) => {
                  const d = new Date(v + "T00:00:00");
                  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
                }}
              />
              <YAxis tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} allowDecimals={false} />
              <Tooltip {...tooltipStyle} />
              <Area type="monotone" dataKey="created" stroke="hsl(38, 92%, 50%)" fill="hsl(38, 92%, 50%)" fillOpacity={0.15} strokeWidth={2} name="Created" />
              <Area type="monotone" dataKey="resolved" stroke="hsl(160, 84%, 39%)" fill="hsl(160, 84%, 39%)" fillOpacity={0.15} strokeWidth={2} name="Resolved" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
