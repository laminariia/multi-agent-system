import { cn, relativeTime } from "~/lib/utils";

export interface ActivityEvent {
  id: string;
  type: "job_found" | "bid_sent" | "hitl_pending" | "hitl_resolved" | "agent_error" | "agent_started" | "delivery_sent";
  message: string;
  timestamp: string;
  agent?: string;
}

const eventConfig: Record<ActivityEvent["type"], { icon: string; color: string }> = {
  job_found: { icon: "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z", color: "text-primary" },
  bid_sent: { icon: "M12 19l9 2-9-18-9 18 9-2zm0 0v-8", color: "text-emerald-400" },
  hitl_pending: { icon: "M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z", color: "text-amber-400" },
  hitl_resolved: { icon: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z", color: "text-emerald-400" },
  agent_error: { icon: "M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z", color: "text-destructive" },
  agent_started: { icon: "M13 10V3L4 14h7v7l9-11h-7z", color: "text-primary" },
  delivery_sent: { icon: "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2", color: "text-primary" },
};

interface ActivityFeedProps {
  events: ActivityEvent[];
  className?: string;
}

export function ActivityFeed({ events, className }: ActivityFeedProps) {
  if (events.length === 0) {
    return (
      <div className={cn("text-center py-8", className)}>
        <p className="text-sm text-muted-foreground">No recent activity</p>
      </div>
    );
  }

  return (
    <div className={cn("space-y-0", className)}>
      {events.map((event) => {
        const config = eventConfig[event.type] ?? eventConfig.agent_started;
        return (
          <div
            key={event.id}
            className="flex items-start gap-3 px-3 py-2.5 hover:bg-accent/30 rounded-md transition-colors"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className={cn("h-4 w-4 mt-0.5 shrink-0", config.color)}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d={config.icon} />
            </svg>
            <div className="min-w-0 flex-1">
              <p className="text-xs text-foreground leading-relaxed">
                {event.message}
              </p>
              <div className="flex items-center gap-2 mt-0.5">
                {event.agent && (
                  <span className="text-[10px] text-primary font-medium">
                    {event.agent}
                  </span>
                )}
                <span className="text-[10px] text-muted-foreground">
                  {relativeTime(event.timestamp)}
                </span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
