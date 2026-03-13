import { NavLink } from "@remix-run/react";
import { Badge } from "~/components/ui/badge";
import { Separator } from "~/components/ui/separator";
import { cn } from "~/lib/utils";

interface NavItem {
  to: string;
  label: string;
  icon: string;
  badge?: number;
  badgeVariant?: "default" | "destructive";
}

interface SidebarNavProps {
  pendingCount: number;
  urgentCount: number;
  collapsed?: boolean;
  pendingUsersCount?: number;
  userRole?: string;
}

export function SidebarNav({ pendingCount, urgentCount, collapsed, pendingUsersCount, userRole }: SidebarNavProps) {
  const pipelineAItems: NavItem[] = [
    {
      to: "/dashboard",
      label: "Dashboard",
      icon: "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6",
    },
    {
      to: "/jobs",
      label: "Jobs",
      icon: "M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2 2v2m4 6h.01M5 20h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z",
    },
    {
      to: "/bids",
      label: "Bid Kanban",
      icon: "M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2",
    },
    {
      to: "/hitl",
      label: "HITL Queue",
      icon: "M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2M9 7a4 4 0 100-8 4 4 0 000 8zM22 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75",
      badge: pendingCount > 0 ? pendingCount : undefined,
      badgeVariant: urgentCount > 0 ? "destructive" : "default",
    },
    {
      to: "/telegram-channels",
      label: "TG Channels",
      icon: "M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z",
    },
  ];

  const pipelineBItems: NavItem[] = [
    {
      to: "/leads",
      label: "Leads",
      icon: "M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2M9 7a4 4 0 100-8 4 4 0 000 8zM23 11h-6M20 8v6",
    },
    {
      to: "/outreach",
      label: "Outreach",
      icon: "M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z",
    },
    {
      to: "/geo",
      label: "Geo Scout",
      icon: "M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z M15 11a3 3 0 11-6 0 3 3 0 016 0z",
    },
  ];

  const systemItems: NavItem[] = [
    {
      to: "/orchestrator",
      label: "Orchestrator",
      icon: "M13 10V3L4 14h7v7l9-11h-7z",
    },
    {
      to: "/agents",
      label: "Agents",
      icon: "M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z",
    },
    {
      to: "/analytics",
      label: "Analytics",
      icon: "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z",
    },
    {
      to: "/portfolio",
      label: "Portfolio",
      icon: "M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z",
    },
    {
      to: "/projects",
      label: "Projects",
      icon: "M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z",
    },
    ...(userRole === "owner" || userRole === "co_owner"
      ? [
          {
            to: "/users",
            label: "Users",
            icon: "M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2M9 7a4 4 0 100-8 4 4 0 000 8zM23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75",
            badge: (pendingUsersCount ?? 0) > 0 ? pendingUsersCount : undefined,
          } as NavItem,
        ]
      : []),
    {
      to: "/settings",
      label: "Settings",
      icon: "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z",
    },
  ];

  return (
    <nav className="flex-1 px-3 py-4 space-y-4 overflow-y-auto">
      <NavSection label="Pipeline A" items={pipelineAItems} collapsed={collapsed} />
      <Separator className="mx-2" />
      <NavSection label="Pipeline B" items={pipelineBItems} collapsed={collapsed} />
      <Separator className="mx-2" />
      <NavSection label="System" items={systemItems} collapsed={collapsed} />
    </nav>
  );
}

function NavSection({
  label,
  items,
  collapsed,
}: {
  label: string;
  items: NavItem[];
  collapsed?: boolean;
}) {
  return (
    <div className="space-y-1">
      {!collapsed && (
        <p className="px-3 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60 mb-2">
          {label}
        </p>
      )}
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            cn(
              "flex items-center justify-between rounded-md px-3 py-2 text-sm font-medium transition-colors",
              isActive
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-accent hover:text-foreground"
            )
          }
        >
          <span className="flex items-center gap-2.5">
            <svg
              className="h-4 w-4 shrink-0"
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d={item.icon} />
            </svg>
            {!collapsed && item.label}
          </span>
          {item.badge != null && !collapsed && (
            <Badge
              variant={item.badgeVariant ?? "default"}
              className="h-5 min-w-[20px] justify-center text-[10px] px-1.5"
            >
              {item.badge}
            </Badge>
          )}
        </NavLink>
      ))}
    </div>
  );
}
