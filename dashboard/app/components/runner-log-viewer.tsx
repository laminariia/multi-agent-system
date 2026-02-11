import { useState, useRef, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Button } from "~/components/ui/button";
import { ScrollArea } from "~/components/ui/scroll-area";
import { Skeleton } from "~/components/ui/skeleton";
import { fetchOrchestratorLogs } from "~/lib/api";
import { cn } from "~/lib/utils";

const levelColors: Record<string, string> = {
  INFO: "text-muted-foreground",
  WARN: "text-amber-400",
  ERROR: "text-destructive",
};

const levelBg: Record<string, string> = {
  INFO: "",
  WARN: "bg-amber-400/5",
  ERROR: "bg-destructive/5",
};

export function RunnerLogViewer() {
  const [lineCount, setLineCount] = useState(40);
  const bottomRef = useRef<HTMLDivElement>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["orch-logs", lineCount],
    queryFn: () => fetchOrchestratorLogs({ n: lineCount }),
    refetchInterval: 30_000,
  });

  useEffect(() => {
    if (bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [data?.lines.length]);

  if (isLoading && !data) {
    return <Skeleton className="h-[400px]" />;
  }

  const lines = data?.lines ?? [];

  return (
    <Card className="border-border/50 animate-fade-in">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base font-medium">Runner Logs</CardTitle>
          {data?.log_file && (
            <span className="text-[10px] text-muted-foreground/60 font-mono truncate max-w-[200px]">
              {data.log_file}
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {lines.length === 0 ? (
          <div className="flex items-center justify-center py-8">
            <p className="text-sm text-muted-foreground">No logs available</p>
          </div>
        ) : (
          <>
            <ScrollArea className="h-[360px] rounded-md border border-border/50 bg-background/50">
              <div className="p-2 font-mono text-xs space-y-0.5">
                {lines.map((line, i) => (
                  <div
                    key={i}
                    className={cn(
                      "flex gap-2 rounded px-2 py-0.5 hover:bg-accent/30 transition-colors",
                      levelBg[line.level] ?? ""
                    )}
                  >
                    {line.timestamp && (
                      <span className="text-muted-foreground/60 shrink-0 w-[60px]">
                        {new Date(line.timestamp).toLocaleTimeString("en-US", { hour12: false })}
                      </span>
                    )}
                    <span
                      className={cn(
                        "shrink-0 w-[40px] font-medium",
                        levelColors[line.level] ?? "text-muted-foreground"
                      )}
                    >
                      {line.level}
                    </span>
                    <span className="text-foreground/90 break-all">
                      {line.line}
                    </span>
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>
            </ScrollArea>

            {data && lines.length >= lineCount && (
              <div className="mt-3 flex justify-center">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setLineCount((prev) => prev + 20)}
                >
                  Load More
                </Button>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
