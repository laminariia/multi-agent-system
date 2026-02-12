import { useRouteError, isRouteErrorResponse, Link } from "@remix-run/react";

/**
 * Reusable error boundary for route segments.
 * Place as a named `ErrorBoundary` export in any route file.
 */
export function RouteErrorBoundary() {
  const error = useRouteError();
  const isResponse = isRouteErrorResponse(error);

  return (
    <div className="flex flex-col items-center justify-center py-20 px-4 animate-fade-in">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-destructive/10 text-destructive mb-4">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className="h-8 w-8"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="8" x2="12" y2="12" />
          <line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
      </div>
      <h2 className="text-lg font-semibold text-foreground">
        {isResponse ? `${error.status} - ${error.statusText}` : "Something went wrong"}
      </h2>
      <p className="mt-1 text-sm text-muted-foreground max-w-md text-center">
        {isResponse
          ? "The requested resource could not be loaded."
          : error instanceof Error
          ? error.message
          : "An unexpected error occurred."}
      </p>
      <div className="flex gap-3 mt-6">
        <button
          onClick={() => window.location.reload()}
          className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-accent transition-colors"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="h-4 w-4"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
            <path d="M3 3v5h5" />
            <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
            <path d="M16 16h5v5" />
          </svg>
          Retry
        </button>
        <Link
          to="/dashboard"
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          Dashboard
        </Link>
      </div>
    </div>
  );
}
