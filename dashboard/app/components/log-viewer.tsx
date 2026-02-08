import { useRef, useEffect } from "react";
import { ScrollArea } from "~/components/ui/scroll-area";
import type { AgentLog } from "~/lib/types";
import { cn } from "~/lib/utils";

const levelColors: Record<string, string> = {
  info: "text-primary",
  warning: "text-amber-400",
  error: "text-destructive",
};

const levelBg: Record<string, string> = {
  info: "bg-primary/5",
  warning: "bg-amber-400/5",
  error: "bg-destructive/5",
};

interface LogViewerProps {
  logs: AgentLog[];
  className?: string;
  autoScroll?: boolean;
}

export function LogViewer({ logs, className, autoScroll = true }: LogViewerProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs.length, autoScroll]);

  if (logs.length === 0) {
    return (
      <div className={cn("flex items-center justify-center py-8", className)}>
        <p className="text-sm text-muted-foreground">No logs available</p>
      </div>
    );
  }

  return (
    <ScrollArea className={cn("h-[400px] rounded-md border border-border/50 bg-background/50", className)}>
      <div className="p-2 font-mono text-xs space-y-0.5">
        {logs.map((log) => {
          const ts = new Date(log.timestamp);
          const time = ts.toLocaleTimeString("en-US", { hour12: false });
          return (
            <div
              key={log.id}
              className={cn(
                "flex gap-2 rounded px-2 py-1 hover:bg-accent/30 transition-colors",
                levelBg[log.level] ?? ""
              )}
            >
              <span className="text-muted-foreground shrink-0 w-[60px]">
                {time}
              </span>
              <span
                className={cn(
                  "shrink-0 w-[52px] font-medium uppercase",
                  levelColors[log.level] ?? "text-muted-foreground"
                )}
              >
                {log.level}
              </span>
              <span className="text-muted-foreground shrink-0">
                [{log.event_type}]
              </span>
              <span className="text-foreground/90 break-all">
                {log.message || "—"}
              </span>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  );
}
