import { lazy, Suspense } from "react";
import { Skeleton } from "~/components/ui/skeleton";
import type { Lead } from "~/lib/types";

const GeoHexMap = lazy(() => import("./geo-hex-map"));

interface LazyHexMapProps {
  leads?: Lead[];
  onHexClick?: (leads: Lead[]) => void;
  selectedCategory?: string;
  className?: string;
}

export function LazyHexMap({
  leads = [],
  onHexClick,
  selectedCategory,
  className,
}: LazyHexMapProps) {
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
      <GeoHexMap
        leads={leads}
        onHexClick={onHexClick}
        selectedCategory={selectedCategory}
        className={className}
      />
    </Suspense>
  );
}
