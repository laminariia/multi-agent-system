import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Skeleton } from "~/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "~/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { fetchPortfolio, createPortfolioProject } from "~/lib/api";
import type { PortfolioProject } from "~/lib/types";
import { toast } from "~/hooks/use-toast";

function formatDate(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    year: "numeric",
  });
}

function PortfolioCard({ project }: { project: PortfolioProject }) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden hover:border-zinc-700 transition-colors">
      {/* Image placeholder */}
      <div className="h-40 bg-zinc-800 flex items-center justify-center">
        {project.image_url ? (
          <img
            src={project.image_url}
            alt={project.title}
            className="w-full h-full object-cover"
          />
        ) : (
          <svg
            className="h-10 w-10 text-zinc-600"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1}
              d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
            />
          </svg>
        )}
      </div>

      {/* Card body */}
      <div className="p-4 space-y-2">
        <p className="font-semibold text-white text-sm line-clamp-1">
          {project.title}
        </p>
        <div className="flex items-center gap-2 flex-wrap">
          {project.category && (
            <Badge className="bg-orange-500/20 text-orange-400 text-[10px]">
              {project.category}
            </Badge>
          )}
          {!project.visible && (
            <Badge className="bg-zinc-700 text-zinc-400 text-[10px]">
              Hidden
            </Badge>
          )}
          <span className="text-[11px] text-zinc-500 ml-auto">
            {formatDate(project.completed_at ?? project.created_at)}
          </span>
        </div>
        {project.tags && project.tags.length > 0 && (
          <div className="flex flex-wrap gap-1 pt-1">
            {project.tags.slice(0, 3).map((t) => (
              <span
                key={t}
                className="bg-zinc-800 text-zinc-400 text-[10px] px-1.5 py-0.5 rounded"
              >
                {t}
              </span>
            ))}
          </div>
        )}
        {project.client_name && (
          <p className="text-[11px] text-zinc-500">{project.client_name}</p>
        )}
      </div>
    </div>
  );
}

const CATEGORIES = [
  "Landing Page",
  "Web App",
  "Mobile App",
  "E-commerce",
  "Dashboard",
  "Branding",
  "Other",
];

interface AddProjectForm {
  title: string;
  category: string;
  description: string;
  tags: string;
  demo_url: string;
  source_url: string;
  client_name: string;
}

const EMPTY_FORM: AddProjectForm = {
  title: "",
  category: "",
  description: "",
  tags: "",
  demo_url: "",
  source_url: "",
  client_name: "",
};

function AddProjectDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<AddProjectForm>(EMPTY_FORM);

  const set = (field: keyof AddProjectForm) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ) => setForm((prev) => ({ ...prev, [field]: e.target.value }));

  const mutation = useMutation({
    mutationFn: () =>
      createPortfolioProject({
        title: form.title,
        category: form.category || undefined,
        description: form.description || undefined,
        tags: form.tags
          ? form.tags.split(",").map((s) => s.trim()).filter(Boolean)
          : undefined,
        demo_url: form.demo_url || undefined,
        source_url: form.source_url || undefined,
        client_name: form.client_name || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
      toast({ title: "Project added", variant: "success" });
      onClose();
      setForm(EMPTY_FORM);
    },
    onError: (err) => {
      toast({
        title: "Failed to add project",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    },
  });

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-zinc-900 border-zinc-800 text-white max-w-md">
        <DialogHeader>
          <DialogTitle>Add Portfolio Project</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-2">
          <div>
            <label className="text-xs text-zinc-400 mb-1 block">Title *</label>
            <Input
              value={form.title}
              onChange={set("title")}
              placeholder="Project title"
              className="bg-zinc-800 border-zinc-700"
            />
          </div>
          <div>
            <label className="text-xs text-zinc-400 mb-1 block">
              Category
            </label>
            <Select
              value={form.category}
              onValueChange={(v) =>
                setForm((prev) => ({ ...prev, category: v }))
              }
            >
              <SelectTrigger className="bg-zinc-800 border-zinc-700">
                <SelectValue placeholder="Select category" />
              </SelectTrigger>
              <SelectContent>
                {CATEGORIES.map((c) => (
                  <SelectItem key={c} value={c}>
                    {c}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-xs text-zinc-400 mb-1 block">
              Description
            </label>
            <textarea
              value={form.description}
              onChange={set("description")}
              placeholder="Brief project description"
              rows={3}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-md px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-orange-500 resize-none"
            />
          </div>
          <div>
            <label className="text-xs text-zinc-400 mb-1 block">
              Tags (comma-separated)
            </label>
            <Input
              value={form.tags}
              onChange={set("tags")}
              placeholder="React, TypeScript, Tailwind"
              className="bg-zinc-800 border-zinc-700"
            />
          </div>
          <div>
            <label className="text-xs text-zinc-400 mb-1 block">
              Client Name
            </label>
            <Input
              value={form.client_name}
              onChange={set("client_name")}
              placeholder="Client or company name"
              className="bg-zinc-800 border-zinc-700"
            />
          </div>
          <div>
            <label className="text-xs text-zinc-400 mb-1 block">Demo URL</label>
            <Input
              value={form.demo_url}
              onChange={set("demo_url")}
              placeholder="https://..."
              className="bg-zinc-800 border-zinc-700"
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={onClose}
            className="border-zinc-700"
          >
            Cancel
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!form.title.trim() || mutation.isPending}
            className="bg-orange-500 hover:bg-orange-600 text-white"
          >
            {mutation.isPending ? "Adding..." : "Add Project"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function PortfolioPage() {
  const [visibilityTab, setVisibilityTab] = useState("all");
  const [search, setSearch] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["portfolio", visibilityTab, search],
    queryFn: () =>
      fetchPortfolio({
        visible:
          visibilityTab === "published"
            ? true
            : visibilityTab === "hidden"
            ? false
            : undefined,
      }),
  });

  const projects = data?.projects ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-white">
            Portfolio
          </h1>
          <Badge className="bg-zinc-800 text-zinc-300 text-xs">{total}</Badge>
        </div>
        <Button
          onClick={() => setDialogOpen(true)}
          className="bg-orange-500 hover:bg-orange-600 text-white"
          size="sm"
        >
          Add Project
        </Button>
      </div>

      {/* Tabs + Search */}
      <div className="flex items-center justify-between gap-4">
        <Tabs value={visibilityTab} onValueChange={setVisibilityTab}>
          <TabsList className="bg-zinc-900 border border-zinc-800">
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="published">Published</TabsTrigger>
            <TabsTrigger value="hidden">Hidden</TabsTrigger>
          </TabsList>
        </Tabs>
        <Input
          placeholder="Search projects..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-56 bg-zinc-900 border-zinc-700"
        />
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load portfolio:{" "}
          {error instanceof Error ? error.message : "Unknown error"}
        </div>
      )}

      {/* Grid */}
      {isLoading ? (
        <div className="grid grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-64" />
          ))}
        </div>
      ) : projects.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <p className="text-zinc-400 text-base font-medium">No projects yet</p>
          <p className="text-zinc-600 text-sm mt-1">
            Add your first portfolio project to get started.
          </p>
          <Button
            onClick={() => setDialogOpen(true)}
            className="mt-4 bg-orange-500 hover:bg-orange-600 text-white"
            size="sm"
          >
            Add Project
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-4">
          {projects.map((project) => (
            <PortfolioCard key={project.id} project={project} />
          ))}
        </div>
      )}

      <AddProjectDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />
    </div>
  );
}
