import { Card, CardContent } from "~/components/ui/card";

export function ErrorState({ message }: { message?: string }) {
  return (
    <Card className="border-destructive/30">
      <CardContent className="flex flex-col items-center justify-center py-8">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className="h-6 w-6 text-destructive mb-2"
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
        <p className="text-sm font-medium text-destructive">Failed to load</p>
        {message && (
          <p className="text-xs text-muted-foreground mt-1 max-w-xs text-center">{message}</p>
        )}
      </CardContent>
    </Card>
  );
}
