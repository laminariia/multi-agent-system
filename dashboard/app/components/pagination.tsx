import { Button } from "~/components/ui/button";
import { cn } from "~/lib/utils";

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  className?: string;
}

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  className,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  if (totalPages <= 1) return null;

  const from = page * pageSize + 1;
  const to = Math.min((page + 1) * pageSize, total);

  // Build page numbers to show (max 5 around current)
  const pages: (number | "...")[] = [];
  const addPage = (p: number) => {
    if (p >= 0 && p < totalPages && !pages.includes(p)) pages.push(p);
  };

  addPage(0);
  if (page > 2) pages.push("...");
  for (let i = Math.max(1, page - 1); i <= Math.min(totalPages - 2, page + 1); i++) {
    addPage(i);
  }
  if (page < totalPages - 3) pages.push("...");
  if (totalPages > 1) addPage(totalPages - 1);

  return (
    <nav aria-label="Pagination" className={cn("flex items-center justify-between", className)}>
      <p className="text-sm text-muted-foreground">
        {from}–{to} of {total}
      </p>
      <div className="flex items-center gap-1">
        <Button
          variant="outline"
          size="sm"
          disabled={page === 0}
          onClick={() => onPageChange(page - 1)}
          className="h-8 w-8 p-0"
          aria-label="Previous page"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6"/></svg>
        </Button>
        {pages.map((p, i) =>
          p === "..." ? (
            <span key={`dots-${i}`} className="px-1 text-muted-foreground text-sm" aria-label="More pages">
              <span aria-hidden="true">...</span>
            </span>
          ) : (
            <Button
              key={p}
              variant={p === page ? "default" : "outline"}
              size="sm"
              onClick={() => onPageChange(p)}
              className="h-8 w-8 p-0 text-xs"
              aria-label={`Page ${p + 1}`}
              aria-current={p === page ? "page" : undefined}
            >
              {p + 1}
            </Button>
          )
        )}
        <Button
          variant="outline"
          size="sm"
          disabled={page >= totalPages - 1}
          onClick={() => onPageChange(page + 1)}
          className="h-8 w-8 p-0"
          aria-label="Next page"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m9 18 6-6-6-6"/></svg>
        </Button>
      </div>
    </nav>
  );
}
