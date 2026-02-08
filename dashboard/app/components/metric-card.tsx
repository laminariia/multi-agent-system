import { Card, CardContent } from "~/components/ui/card";
import { cn } from "~/lib/utils";

interface MetricCardProps {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  trend?: {
    value: number;
    positive?: boolean;
  };
  className?: string;
}

export function MetricCard({ icon, label, value, trend, className }: MetricCardProps) {
  return (
    <Card className={cn("border-border/50 animate-fade-in", className)}>
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
            {icon}
          </div>
          {trend && (
            <span
              className={cn(
                "flex items-center gap-0.5 text-xs font-medium",
                trend.positive ? "text-emerald-400" : "text-rose-400"
              )}
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="12"
                height="12"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className={trend.positive ? "" : "rotate-180"}
              >
                <path d="m18 15-6-6-6 6" />
              </svg>
              {Math.abs(trend.value)}%
            </span>
          )}
        </div>
        <div className="mt-3">
          <p className="text-2xl font-bold tracking-tight">{value}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{label}</p>
        </div>
      </CardContent>
    </Card>
  );
}
