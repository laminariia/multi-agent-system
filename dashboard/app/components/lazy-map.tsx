import { lazy, Suspense } from "react";
import { Skeleton } from "~/components/ui/skeleton";
import type { Lead } from "~/lib/types";

const LeadMap = lazy(() => import("./lead-map"));

interface LazyMapProps {
  leads?: Lead[];
  onMarkerClick?: (id: string) => void;
  className?: string;
}

export function LazyMap({ leads = [], onMarkerClick, className }: LazyMapProps) {
  if (typeof window === "undefined") {
    return (
      <div className={className}>
        <Skeleton className="h-[300px] w-full rounded-lg" />
      </div>
    );
  }

  return (
    <Suspense
      fallback={
        <div className={className}>
          <Skeleton className="h-[300px] w-full rounded-lg" />
        </div>
      }
    >
      <LeadMap leads={leads} onMarkerClick={onMarkerClick} className={className} />
    </Suspense>
  );
}
