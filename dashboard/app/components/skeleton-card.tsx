import { Card, CardContent, CardHeader } from "~/components/ui/card";
import { Skeleton } from "~/components/ui/skeleton";
import { cn } from "~/lib/utils";

interface SkeletonCardProps {
  variant?: "metric" | "agent" | "job" | "default";
  className?: string;
}

export function SkeletonCard({ variant = "default", className }: SkeletonCardProps) {
  if (variant === "metric") {
    return (
      <Card className={cn("border-border/50", className)}>
        <CardContent className="p-5">
          <div className="flex items-center justify-between">
            <Skeleton className="h-10 w-10 rounded-lg" />
            <Skeleton className="h-4 w-12" />
          </div>
          <div className="mt-3 space-y-1.5">
            <Skeleton className="h-7 w-16" />
            <Skeleton className="h-3 w-24" />
          </div>
        </CardContent>
      </Card>
    );
  }

  if (variant === "agent") {
    return (
      <Card className={cn("border-border/50", className)}>
        <CardContent className="p-4">
          <div className="flex items-start justify-between mb-3">
            <div className="flex items-center gap-2.5">
              <Skeleton className="h-2.5 w-2.5 rounded-full" />
              <Skeleton className="h-4 w-24" />
            </div>
            <Skeleton className="h-5 w-16 rounded-full" />
          </div>
          <div className="space-y-2">
            <Skeleton className="h-5 w-16 rounded-full" />
            <Skeleton className="h-3 w-full" />
            <div className="flex items-center justify-between pt-1 border-t border-border/30">
              <Skeleton className="h-3 w-20" />
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (variant === "job") {
    return (
      <Card className={cn("border-border/50 p-4", className)}>
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2.5">
            <Skeleton className="h-8 w-8 rounded" />
            <div className="space-y-1.5">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-3 w-20" />
            </div>
          </div>
          <Skeleton className="h-5 w-16 rounded-full" />
        </div>
        <div className="flex gap-1 mb-3">
          <Skeleton className="h-5 w-14 rounded-full" />
          <Skeleton className="h-5 w-18 rounded-full" />
          <Skeleton className="h-5 w-12 rounded-full" />
        </div>
        <div className="flex items-center justify-between">
          <Skeleton className="h-5 w-20" />
          <Skeleton className="h-3 w-16" />
        </div>
      </Card>
    );
  }

  // default
  return (
    <Card className={cn("border-border/50", className)}>
      <CardHeader className="pb-3">
        <Skeleton className="h-5 w-32" />
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      </CardContent>
    </Card>
  );
}

interface SkeletonGridProps {
  count?: number;
  variant?: SkeletonCardProps["variant"];
  columns?: string;
  className?: string;
}

export function SkeletonGrid({
  count = 6,
  variant = "default",
  columns = "sm:grid-cols-2 xl:grid-cols-3",
  className,
}: SkeletonGridProps) {
  return (
    <div className={cn("grid gap-3", columns, className)}>
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} variant={variant} />
      ))}
    </div>
  );
}
