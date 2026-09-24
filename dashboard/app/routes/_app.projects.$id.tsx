import { useState } from "react";
import { useParams, Link } from "@remix-run/react";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Separator } from "~/components/ui/separator";
import { Skeleton } from "~/components/ui/skeleton";
import { Progress } from "~/components/ui/progress";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { fetchProject } from "~/lib/api";
import type { ProjectTask, ProjectArtifact, ProjectRevision } from "~/lib/types";
import { relativeTime } from "~/lib/utils";
import { useWsSubscription } from "~/hooks/use-ws-subscription";

/* ---------- helpers ---------- */

function formatCurrency(v: number | null): string {
  if (v == null) return "--";
  return `$${v.toLocaleString()}`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "--";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatBytes(bytes: number | null): string {
  if (bytes == null || bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

/* ---------- status config ---------- */

const STATUS_COLORS: Record<string, string> = {
  active: "bg-blue-500/20 text-blue-400",
  completed: "bg-green-500/20 text-green-400",
  on_hold: "bg-yellow-500/20 text-yellow-400",
  cancelled: "bg-red-500/20 text-red-400",
};

const TASK_STATUS_COLORS: Record<ProjectTask["status"], string> = {
  pending: "bg-zinc-600 text-zinc-200",
  in_progress: "bg-orange-500/20 text-orange-400",
  completed: "bg-green-500/20 text-green-400",
  failed: "bg-red-500/20 text-red-400",
};

const TYPE_COLORS: Record<string, string> = {
  freelance: "bg-blue-500/20 text-blue-400",
  direct: "bg-purple-500/20 text-purple-400",
  internal: "bg-cyan-500/20 text-cyan-400",
};

/* ---------- kanban column indicator ---------- */

function KanbanIndicator({ status }: { status: string }) {
  const columns = [
    { key: "backlog", label: "Backlog" },
    { key: "active", label: "In Progress" },
    { key: "on_hold", label: "On Hold" },
    { key: "completed", label: "Done" },
  ];

  const statusToColumn: Record<string, string> = {
    active: "active",
    completed: "completed",
    on_hold: "on_hold",
    cancelled: "completed",
  };

  const currentColumn = statusToColumn[status] ?? "backlog";

  return (
    <div className="flex items-center gap-1">
      {columns.map((col, idx) => {
        const isActive = col.key === currentColumn;
        const isPast =
          columns.findIndex((c) => c.key === currentColumn) > idx;

        return (
          <div key={col.key} className="flex items-center">
            <div className="flex flex-col items-center gap-0.5">
              <div
                className={`h-2 w-2 rounded-full ${
                  isPast
                    ? "bg-green-500"
                    : isActive
                    ? "bg-orange-500"
                    : "bg-zinc-700"
                }`}
              />
              <span
                className={`text-[8px] ${
                  isActive ? "text-orange-400 font-medium" : "text-zinc-600"
                }`}
              >
                {col.label}
              </span>
            </div>
            {idx < columns.length - 1 && (
              <div
                className={`h-0.5 w-6 mb-3 ${
                  isPast ? "bg-green-500" : "bg-zinc-700"
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ---------- task row ---------- */

function TaskRow({ task }: { task: ProjectTask }) {
  return (
    <div className="flex items-center justify-between py-3 px-1">
      <div className="flex items-center gap-3 min-w-0 flex-1">
        <div
          className={`h-2 w-2 rounded-full shrink-0 ${
            task.status === "completed"
              ? "bg-green-500"
              : task.status === "in_progress"
              ? "bg-orange-500"
              : task.status === "failed"
              ? "bg-red-500"
              : "bg-zinc-600"
          }`}
        />
        <span className="text-sm text-white truncate">{task.title}</span>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        {task.agent && (
          <span className="text-[10px] bg-zinc-800 text-zinc-400 px-2 py-0.5 rounded">
            {task.agent}
          </span>
        )}
        <Badge className={`text-[10px] capitalize ${TASK_STATUS_COLORS[task.status]}`}>
          {task.status.replace("_", " ")}
        </Badge>
        {(task.estimated_hours != null || task.actual_hours != null) && (
          <span className="text-[10px] text-zinc-500 w-16 text-right">
            {task.actual_hours ?? 0}h / {task.estimated_hours ?? "?"}h
          </span>
        )}
      </div>
    </div>
  );
}

/* ---------- artifact row ---------- */

function ArtifactRow({ artifact }: { artifact: ProjectArtifact }) {
  const typeIcons: Record<string, string> = {
    code: "{ }",
    document: "DOC",
    image: "IMG",
    design: "DSN",
    archive: "ZIP",
  };

  return (
    <div className="flex items-center justify-between py-3 px-1">
      <div className="flex items-center gap-3 min-w-0 flex-1">
        <span className="text-[10px] bg-zinc-800 text-zinc-400 px-1.5 py-0.5 rounded font-mono w-8 text-center shrink-0">
          {typeIcons[artifact.type] ?? artifact.type.slice(0, 3).toUpperCase()}
        </span>
        <span className="text-sm text-white truncate">{artifact.filename}</span>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <span className="text-[10px] text-zinc-500">
          {formatBytes(artifact.size_bytes)}
        </span>
        <span className="text-[10px] text-zinc-600">
          {relativeTime(artifact.created_at)}
        </span>
        {artifact.url && (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs text-orange-400 hover:text-orange-300"
            asChild
          >
            <a href={artifact.url} target="_blank" rel="noopener noreferrer">
              Download
            </a>
          </Button>
        )}
      </div>
    </div>
  );
}

/* ---------- revision entry ---------- */

function RevisionEntry({ revision }: { revision: ProjectRevision }) {
  return (
    <div className="flex items-start gap-3">
      <div className="mt-1.5 flex-shrink-0">
        <div className="h-2.5 w-2.5 rounded-full bg-purple-500" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-white">
            Revision #{revision.revision_number}
          </span>
          {revision.agent && (
            <span className="text-[10px] bg-zinc-800 text-zinc-400 px-2 py-0.5 rounded">
              {revision.agent}
            </span>
          )}
        </div>
        <p className="text-sm text-zinc-400 mt-0.5">{revision.description}</p>
        <p className="text-xs text-zinc-600 mt-1">
          {new Date(revision.created_at).toLocaleString()}
        </p>
      </div>
    </div>
  );
}

/* ---------- page ---------- */

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [activeTab, setActiveTab] = useState("overview");

  useWsSubscription("subscribe:project", id);

  const {
    data: project,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["project", id],
    queryFn: () => fetchProject(id!),
    enabled: !!id,
  });

  /* ---------- loading ---------- */

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-5 w-32" />
        <div className="flex items-start justify-between gap-4">
          <Skeleton className="h-8 w-80" />
          <Skeleton className="h-9 w-28" />
        </div>
        <Skeleton className="h-4 w-full" />
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2 space-y-4">
            <Skeleton className="h-[200px]" />
            <Skeleton className="h-[150px]" />
          </div>
          <div className="space-y-4">
            <Skeleton className="h-[180px]" />
            <Skeleton className="h-[120px]" />
          </div>
        </div>
      </div>
    );
  }

  /* ---------- error ---------- */

  if (error || !project) {
    return (
      <div className="space-y-4">
        <Link
          to="/projects"
          className="text-sm text-orange-400 hover:underline"
        >
          Back to Projects
        </Link>
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error instanceof Error ? error.message : "Project not found"}
        </div>
      </div>
    );
  }

  const statusCls =
    STATUS_COLORS[project.status] ?? "bg-zinc-700 text-zinc-300";
  const typeCls =
    TYPE_COLORS[project.type ?? ""] ?? "bg-zinc-700 text-zinc-300";
  const progressValue =
    project.progress ??
    (project.status === "completed"
      ? 100
      : project.status === "active"
      ? 50
      : project.status === "on_hold"
      ? 25
      : 0);

  const tasks = project.tasks ?? [];
  const artifacts = project.artifacts_list ?? [];
  const revisions = project.revisions ?? [];
  const completedTasks = tasks.filter((t) => t.status === "completed").length;

  return (
    <div className="space-y-5">
      {/* Breadcrumb */}
      <Link
        to="/projects"
        className="text-sm text-orange-400 hover:underline inline-flex items-center gap-1"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className="h-3 w-3"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="m15 18-6-6 6-6" />
        </svg>
        Back to Projects
      </Link>

      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold tracking-tight text-white leading-tight">
            {project.title}
          </h1>
          <div className="flex items-center gap-3 mt-2 text-sm flex-wrap">
            <Badge className={`text-[10px] capitalize ${statusCls}`}>
              {project.status.replace(/_/g, " ")}
            </Badge>
            {project.type && (
              <Badge className={`text-[10px] capitalize ${typeCls}`}>
                {project.type}
              </Badge>
            )}
            {project.client_name && (
              <span className="text-zinc-400">{project.client_name}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {project.job_id && (
            <Button
              variant="outline"
              size="sm"
              className="border-zinc-700"
              asChild
            >
              <Link to={`/jobs/${project.job_id}`}>View Job</Link>
            </Button>
          )}
          {project.deal_id && (
            <Button
              variant="outline"
              size="sm"
              className="border-zinc-700"
              asChild
            >
              <Link to={`/deals/${project.deal_id}`}>View Deal</Link>
            </Button>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
        <div className="flex items-center justify-between mb-2">
          <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium">
            Progress
          </p>
          <span className="text-sm font-semibold text-orange-400">
            {progressValue}%
          </span>
        </div>
        <Progress value={progressValue} className="h-2 bg-zinc-700" />
        <div className="flex items-center justify-between mt-3">
          <KanbanIndicator status={project.status} />
          {tasks.length > 0 && (
            <span className="text-[10px] text-zinc-500">
              {completedTasks} / {tasks.length} tasks done
            </span>
          )}
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="bg-zinc-900 border border-zinc-800">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="tasks">
            Tasks
            {tasks.length > 0 && (
              <span className="ml-1.5 text-[10px] text-zinc-500">
                {tasks.length}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="artifacts">
            Artifacts
            {artifacts.length > 0 && (
              <span className="ml-1.5 text-[10px] text-zinc-500">
                {artifacts.length}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="timeline">
            Timeline
            {revisions.length > 0 && (
              <span className="ml-1.5 text-[10px] text-zinc-500">
                {revisions.length}
              </span>
            )}
          </TabsTrigger>
        </TabsList>
      </Tabs>

      {/* Main content */}
      <div className="grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-5">
          {/* Overview tab */}
          {activeTab === "overview" && (
            <>
              {/* Stats grid */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {[
                  {
                    label: "Budget",
                    value: formatCurrency(
                      project.agreed_amount ?? project.budget
                    ),
                    sub: "agreed",
                  },
                  {
                    label: "Deadline",
                    value: project.deadline
                      ? formatDate(project.deadline)
                      : "None",
                    sub: project.deadline
                      ? relativeTime(project.deadline)
                      : "",
                  },
                  {
                    label: "Revisions",
                    value: String(project.revision_count ?? revisions.length),
                    sub: "total",
                  },
                  {
                    label: "Tasks",
                    value: `${completedTasks}/${tasks.length}`,
                    sub: "completed",
                  },
                ].map((stat) => (
                  <div
                    key={stat.label}
                    className="bg-zinc-900 border border-zinc-800 rounded-lg px-4 py-3"
                  >
                    <p className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1">
                      {stat.label}
                    </p>
                    <p className="text-sm font-semibold text-white truncate">
                      {stat.value}
                    </p>
                    {stat.sub && (
                      <p className="text-[10px] text-zinc-600 mt-0.5 truncate">
                        {stat.sub}
                      </p>
                    )}
                  </div>
                ))}
              </div>

              {/* Description */}
              <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
                <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-3">
                  Description
                </p>
                {project.description ? (
                  <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
                    {project.description}
                  </p>
                ) : (
                  <p className="text-sm text-zinc-600 italic">
                    No description provided.
                  </p>
                )}
              </div>

              {/* Artifacts preview (raw JSON fallback) */}
              {project.artifacts &&
                Object.keys(project.artifacts).length > 0 && (
                  <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
                    <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-3">
                      Artifacts (raw)
                    </p>
                    <pre className="text-xs bg-zinc-950 p-4 rounded-md overflow-auto max-h-48 text-zinc-400">
                      {JSON.stringify(project.artifacts, null, 2)}
                    </pre>
                  </div>
                )}
            </>
          )}

          {/* Tasks tab */}
          {activeTab === "tasks" && (
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
              {tasks.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12">
                  <p className="text-zinc-500 text-sm">No tasks yet.</p>
                  <p className="text-zinc-600 text-xs mt-1">
                    Tasks will appear when agents begin work.
                  </p>
                </div>
              ) : (
                <div>
                  {/* Header */}
                  <div className="px-5 py-3 border-b border-zinc-800 flex items-center justify-between">
                    <p className="text-sm font-medium text-white">
                      Tasks ({tasks.length})
                    </p>
                    <span className="text-[10px] text-zinc-500">
                      {completedTasks} completed
                    </span>
                  </div>
                  {/* Task list */}
                  <div className="divide-y divide-zinc-800 px-4">
                    {tasks.map((task) => (
                      <TaskRow key={task.id} task={task} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Artifacts tab */}
          {activeTab === "artifacts" && (
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
              {artifacts.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12">
                  <p className="text-zinc-500 text-sm">No artifacts yet.</p>
                  <p className="text-zinc-600 text-xs mt-1">
                    Files will appear as agents produce deliverables.
                  </p>
                </div>
              ) : (
                <div>
                  <div className="px-5 py-3 border-b border-zinc-800 flex items-center justify-between">
                    <p className="text-sm font-medium text-white">
                      Artifacts ({artifacts.length})
                    </p>
                  </div>
                  <div className="divide-y divide-zinc-800 px-4">
                    {artifacts.map((artifact) => (
                      <ArtifactRow key={artifact.id} artifact={artifact} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Timeline tab */}
          {activeTab === "timeline" && (
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-5">
              <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-4">
                Revision History
              </p>
              {revisions.length === 0 ? (
                <div className="text-center py-8">
                  <p className="text-zinc-500 text-sm">No revisions yet.</p>
                </div>
              ) : (
                <div className="space-y-4">
                  {revisions
                    .sort(
                      (a, b) =>
                        new Date(b.created_at).getTime() -
                        new Date(a.created_at).getTime()
                    )
                    .map((rev) => (
                      <RevisionEntry key={rev.id} revision={rev} />
                    ))}
                </div>
              )}

              <Separator className="my-5 bg-zinc-800" />

              {/* Project creation event */}
              <div className="flex items-start gap-3">
                <div className="mt-1.5 flex-shrink-0">
                  <div className="h-2.5 w-2.5 rounded-full bg-blue-500" />
                </div>
                <div className="flex-1">
                  <p className="text-sm text-zinc-300">Project created</p>
                  <p className="text-xs text-zinc-500 mt-0.5">
                    {new Date(project.created_at).toLocaleString()}
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Sidebar */}
        <div className="space-y-4">
          {/* Overview card */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-3">
              Project Info
            </p>
            <div className="space-y-2.5 text-sm">
              {project.client_name && (
                <div className="flex items-center justify-between">
                  <span className="text-zinc-500">Client</span>
                  <span className="text-white">{project.client_name}</span>
                </div>
              )}
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Amount</span>
                <span className="text-orange-400 font-semibold">
                  {formatCurrency(project.agreed_amount ?? project.budget)}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Deadline</span>
                <span className="text-white">
                  {project.deadline ? formatDate(project.deadline) : "None"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Revisions</span>
                <span className="text-white">
                  {project.revision_count ?? revisions.length}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Status</span>
                <Badge className={`text-[10px] capitalize ${statusCls}`}>
                  {project.status.replace(/_/g, " ")}
                </Badge>
              </div>
              {project.type && (
                <div className="flex items-center justify-between">
                  <span className="text-zinc-500">Type</span>
                  <Badge className={`text-[10px] capitalize ${typeCls}`}>
                    {project.type}
                  </Badge>
                </div>
              )}
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Created</span>
                <span className="text-white text-xs">
                  {relativeTime(project.created_at)}
                </span>
              </div>
              {project.updated_at && (
                <div className="flex items-center justify-between">
                  <span className="text-zinc-500">Updated</span>
                  <span className="text-white text-xs">
                    {relativeTime(project.updated_at)}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Navigation */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium mb-3">
              Navigation
            </p>
            <div className="space-y-2">
              {project.job_id && (
                <Button
                  variant="outline"
                  className="w-full border-zinc-700"
                  size="sm"
                  asChild
                >
                  <Link to={`/jobs/${project.job_id}`}>View Source Job</Link>
                </Button>
              )}
              {project.deal_id && (
                <Button
                  variant="outline"
                  className="w-full border-zinc-700"
                  size="sm"
                  asChild
                >
                  <Link to={`/deals/${project.deal_id}`}>View Deal</Link>
                </Button>
              )}
              <Button
                variant="outline"
                className="w-full border-zinc-700"
                size="sm"
                asChild
              >
                <Link to="/projects">All Projects</Link>
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
