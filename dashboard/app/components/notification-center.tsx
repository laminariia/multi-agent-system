import { useNavigate } from "@remix-run/react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import { Button } from "~/components/ui/button";
import { ScrollArea } from "~/components/ui/scroll-area";
import { useNotificationStore, type AppNotification } from "~/stores/notification-store";
import { cn } from "~/lib/utils";

function timeAgo(ts: number): string {
  const diff = Math.floor((Date.now() - ts) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const TYPE_ICONS: Record<AppNotification["type"], string> = {
  hitl: "H",
  agent: "A",
  project: "J",
  orch: "O",
  system: "S",
};

const TYPE_COLORS: Record<AppNotification["type"], string> = {
  hitl: "bg-amber-500/20 text-amber-600 dark:text-amber-400",
  agent: "bg-blue-500/20 text-blue-600 dark:text-blue-400",
  project: "bg-emerald-500/20 text-emerald-600 dark:text-emerald-400",
  orch: "bg-purple-500/20 text-purple-600 dark:text-purple-400",
  system: "bg-gray-500/20 text-gray-600 dark:text-gray-400",
};

interface NotificationCenterProps {
  pendingHITLCount: number;
}

export function NotificationCenter({ pendingHITLCount }: NotificationCenterProps) {
  const navigate = useNavigate();
  const notifications = useNotificationStore((s) => s.notifications);
  const unreadCount = useNotificationStore((s) => s.unreadCount);
  const markAllRead = useNotificationStore((s) => s.markAllRead);
  const markRead = useNotificationStore((s) => s.markRead);

  const totalBadge = unreadCount + pendingHITLCount;

  const handleClick = (n: AppNotification) => {
    markRead(n.id);
    if (n.link) {
      navigate(n.link);
    }
  };

  return (
    <DropdownMenu onOpenChange={(open) => { if (open) markAllRead(); }}>
      <DropdownMenuTrigger asChild>
        <button
          className="relative p-2 rounded-md hover:bg-accent transition-colors"
          aria-label={`${totalBadge} notifications`}
        >
          <svg
            className="h-5 w-5 text-muted-foreground"
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9" />
            <path d="M13.73 21a2 2 0 01-3.46 0" />
          </svg>
          {totalBadge > 0 && (
            <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-bold text-destructive-foreground">
              {totalBadge > 99 ? "99+" : totalBadge}
            </span>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-semibold">Notifications</span>
          {notifications.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="h-auto py-1 px-2 text-xs text-muted-foreground"
              onClick={() => markAllRead()}
            >
              Mark all read
            </Button>
          )}
        </div>

        {pendingHITLCount > 0 && (
          <button
            onClick={() => navigate("/hitl")}
            className="flex w-full items-center gap-3 border-b bg-amber-500/5 px-3 py-2.5 text-left hover:bg-accent transition-colors"
          >
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-amber-500/20 text-xs font-bold text-amber-600 dark:text-amber-400">
              {pendingHITLCount}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium truncate">HITL items pending</p>
              <p className="text-xs text-muted-foreground">Click to review</p>
            </div>
            <svg className="h-4 w-4 text-muted-foreground flex-shrink-0" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="m9 18 6-6-6-6" />
            </svg>
          </button>
        )}

        <ScrollArea className="max-h-[320px]">
          {notifications.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 text-center">
              <svg className="h-8 w-8 text-muted-foreground/40 mb-2" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9" />
                <path d="M13.73 21a2 2 0 01-3.46 0" />
              </svg>
              <p className="text-xs text-muted-foreground">No recent notifications</p>
            </div>
          ) : (
            <div>
              {notifications.slice(0, 20).map((n) => (
                <button
                  key={n.id}
                  onClick={() => handleClick(n)}
                  className={cn(
                    "flex w-full items-start gap-3 px-3 py-2.5 text-left transition-colors hover:bg-accent",
                    !n.read && "bg-primary/[0.03]"
                  )}
                >
                  <span
                    className={cn(
                      "mt-0.5 flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-bold flex-shrink-0",
                      TYPE_COLORS[n.type]
                    )}
                  >
                    {TYPE_ICONS[n.type]}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <p className={cn("text-sm truncate", !n.read && "font-medium")}>
                        {n.title}
                      </p>
                      <span className="text-[10px] text-muted-foreground whitespace-nowrap flex-shrink-0">
                        {timeAgo(n.timestamp)}
                      </span>
                    </div>
                    {n.description && (
                      <p className="text-xs text-muted-foreground truncate mt-0.5">
                        {n.description}
                      </p>
                    )}
                  </div>
                  {!n.read && (
                    <span className="mt-2 h-1.5 w-1.5 rounded-full bg-primary flex-shrink-0" />
                  )}
                </button>
              ))}
            </div>
          )}
        </ScrollArea>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
