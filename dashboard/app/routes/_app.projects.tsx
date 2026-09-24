import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Skeleton } from "~/components/ui/skeleton";
import { fetchProjects } from "~/lib/api";
import type { Project } from "~/lib/types";

function formatDate(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

function formatBudget(v: number | null): string {
  if (v == null) return "";
  return `$${v.toLocaleString()}`;
}

// Map Project.status to kanban columns
const COLUMNS: { key: string; label: string; statuses: Project["status"][] }[] =
  [
    { key: "backlog", label: "Backlog", statuses: [] },
    { key: "in_progress", label: "In Progress", statuses: ["active"] },
    { key: "review", label: "On Hold", statuses: ["on_hold"] },
    {
      key: "done",
      label: "Done",
      statuses: ["completed", "cancelled"],
    },
  ];

function ProjectCard({ project }: { project: Project }) {
  const statusColors: Record<Project["status"], string> = {
    active: "bg-blue-500/20 text-blue-400",
    completed: "bg-green-500/20 text-green-400",
    on_hold: "bg-yellow-500/20 text-yellow-400",
    cancelled: "bg-red-500/20 text-red-400",
  };

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <p className="font-semibold text-white text-sm leading-snug line-clamp-2">
          {project.title}
        </p>
        <Badge
          className={`${statusColors[project.status]} text-[10px] shrink-0`}
        >
          {project.status.replace("_", " ")}
        </Badge>
      </div>

      {project.client_name && (
        <p className="text-xs text-zinc-400">{project.client_name}</p>
      )}

      {project.description && (
        <p className="text-xs text-zinc-500 line-clamp-2">
          {project.description}
        </p>
      )}

      {/* Progress bar */}
      <div className="w-full h-1.5 bg-zinc-700 rounded-full mt-2">
        <div
          className="h-full bg-orange-500 rounded-full transition-all"
          style={{
            width: `${
              project.progress ?? (
                project.status === "completed" ? 100
                : project.status === "active" ? 50
                : project.status === "on_hold" ? 25
                : 0
              )
            }%`,
          }}
        />
      </div>

      <div className="flex items-center justify-between pt-1">
        {project.budget != null ? (
          <span className="text-orange-400 font-medium text-xs">
            {formatBudget(project.budget)}
          </span>
        ) : (
          <span />
        )}
        {project.deadline && (
          <span className="text-[11px] text-zinc-500">
            Due {formatDate(project.deadline)}
          </span>
        )}
      </div>
    </div>
  );
}

function KanbanColumn({
  label,
  projects,
  loading,
}: {
  label: string;
  projects: Project[];
  loading: boolean;
}) {
  return (
    <div className="flex flex-col min-h-0">
      <div className="h-1 bg-orange-500 rounded-t-md" />
      <div className="bg-zinc-900/50 border border-zinc-800 border-t-0 rounded-b-md p-3 flex items-center justify-between mb-3">
        <span className="text-sm font-semibold text-white">{label}</span>
        <Badge className="bg-zinc-800 text-zinc-300 text-[10px]">
          {projects.length}
        </Badge>
      </div>
      <div className="flex flex-col gap-3 overflow-y-auto flex-1">
        {loading
          ? Array.from({ length: 2 }).map((_, i) => (
              <Skeleton key={i} className="h-28" />
            ))
          : projects.map((p) => <ProjectCard key={p.id} project={p} />)}
        {!loading && projects.length === 0 && (
          <div className="text-center text-zinc-600 text-xs py-8">Empty</div>
        )}
      </div>
    </div>
  );
}

export default function ProjectsPage() {
  const [pipelineTab, setPipelineTab] = useState("all");
  const [search, setSearch] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["projects", pipelineTab],
    queryFn: () =>
      fetchProjects({
        status: pipelineTab !== "all" ? pipelineTab : undefined,
        limit: 100,
      }),
    refetchInterval: 30_000,
  });

  const allProjects = data?.projects ?? [];

  const filtered = search
    ? allProjects.filter(
        (p) =>
          p.title.toLowerCase().includes(search.toLowerCase()) ||
          (p.client_name ?? "").toLowerCase().includes(search.toLowerCase())
      )
    : allProjects;

  // Projects without a column slot go into backlog
  const assignedStatuses = COLUMNS.flatMap((c) => c.statuses);
  const columns = COLUMNS.map((col) => ({
    ...col,
    projects:
      col.key === "backlog"
        ? filtered.filter((p) => !assignedStatuses.includes(p.status))
        : filtered.filter((p) =>
            (col.statuses as string[]).includes(p.status)
          ),
  }));

  return (
    <div className="flex flex-col h-full space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-4 shrink-0">
        <h1 className="text-2xl font-bold tracking-tight text-white">
          Projects
        </h1>
        <div className="flex items-center gap-2">
          <Input
            placeholder="Search projects..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-56 bg-zinc-900 border-zinc-700"
          />
          <Button variant="outline" size="sm" className="border-zinc-700">
            Filter
          </Button>
          <Button
            size="sm"
            className="bg-orange-500 hover:bg-orange-600 text-white"
          >
            New Project
          </Button>
        </div>
      </div>

      {/* Pipeline tabs */}
      <Tabs
        value={pipelineTab}
        onValueChange={setPipelineTab}
        className="shrink-0"
      >
        <TabsList className="bg-zinc-900 border border-zinc-800">
          <TabsTrigger value="all">All</TabsTrigger>
          <TabsTrigger value="freelance">Freelance</TabsTrigger>
          <TabsTrigger value="under_development">Under Development</TabsTrigger>
        </TabsList>
      </Tabs>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load projects:{" "}
          {error instanceof Error ? error.message : "Unknown error"}
        </div>
      )}

      {/* Kanban */}
      <div className="grid grid-cols-4 gap-4 flex-1 min-h-0">
        {columns.map((col) => (
          <KanbanColumn
            key={col.key}
            label={col.label}
            projects={col.projects}
            loading={isLoading}
          />
        ))}
      </div>
    </div>
  );
}
