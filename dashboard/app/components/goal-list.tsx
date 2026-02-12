import { useState, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogTrigger,
} from "~/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { fetchGoals, addGoal, deleteGoal } from "~/lib/api";
import { toast } from "~/hooks/use-toast";
import type { Goal } from "~/lib/types";
import { cn } from "~/lib/utils";

const statusFilters = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
] as const;

const priorityColors: Record<string, string> = {
  critical: "bg-red-500/20 text-red-400",
  high: "bg-orange-500/20 text-orange-400",
  medium: "bg-amber-500/20 text-amber-400",
  low: "bg-blue-500/20 text-blue-400",
};

const priorityOptions = ["low", "medium", "high", "critical"] as const;

const statusIcons: Record<string, string> = {
  pending: "text-muted-foreground",
  in_progress: "text-primary",
  completed: "text-emerald-400",
  failed: "text-destructive",
};

export function GoalList() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("all");
  const [addOpen, setAddOpen] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newPriority, setNewPriority] = useState("medium");
  const [newCategory, setNewCategory] = useState("feature");
  const [quickAddValue, setQuickAddValue] = useState("");
  const [quickAddding, setQuickAdding] = useState(false);
  const quickAddRef = useRef<HTMLInputElement>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["orch-goals", filter],
    queryFn: () => fetchGoals({ status: filter === "all" ? undefined : filter }),
    refetchInterval: 30_000,
  });

  const addMutation = useMutation({
    mutationFn: () => addGoal(newTitle, newPriority, newCategory),
    onSuccess: (res) => {
      toast({ title: "Goal added", description: res.message });
      queryClient.invalidateQueries({ queryKey: ["orch-goals"] });
      queryClient.invalidateQueries({ queryKey: ["orch-status"] });
      setAddOpen(false);
      setNewTitle("");
      setNewPriority("medium");
      setNewCategory("feature");
    },
    onError: (err) => {
      toast({
        title: "Failed to add goal",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (goalId: string) => deleteGoal(goalId),
    onSuccess: (res) => {
      toast({ title: "Goal deleted", description: res.message });
      queryClient.invalidateQueries({ queryKey: ["orch-goals"] });
      queryClient.invalidateQueries({ queryKey: ["orch-status"] });
    },
    onError: (err) => {
      toast({
        title: "Failed to delete goal",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    },
  });

  const handleQuickAdd = async () => {
    const title = quickAddValue.trim();
    if (!title) return;

    setQuickAdding(true);
    // Optimistic: add a temporary goal to the list
    const tempId = `temp-${Date.now()}`;
    const optimisticGoal: Goal = {
      id: tempId,
      title,
      priority: "medium",
      category: "feature",
      status: "pending",
      completed_at: null,
      result: null,
    };

    queryClient.setQueryData(
      ["orch-goals", filter],
      (old: { goals: Goal[]; total: number; pending: number; completed: number; failed: number } | undefined) => {
        if (!old) return old;
        return {
          ...old,
          goals: [optimisticGoal, ...old.goals],
          total: old.total + 1,
          pending: old.pending + 1,
        };
      }
    );

    setQuickAddValue("");

    try {
      await addGoal(title);
      queryClient.invalidateQueries({ queryKey: ["orch-goals"] });
      queryClient.invalidateQueries({ queryKey: ["orch-status"] });
    } catch (err) {
      // Rollback optimistic update
      queryClient.invalidateQueries({ queryKey: ["orch-goals"] });
      toast({
        title: "Failed to add goal",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setQuickAdding(false);
      quickAddRef.current?.focus();
    }
  };

  const goals = data?.goals ?? [];

  return (
    <Card className="border-border/50 animate-fade-in">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base font-medium">Goals</CardTitle>
          <Dialog open={addOpen} onOpenChange={setAddOpen}>
            <DialogTrigger asChild>
              <Button size="sm" variant="outline">
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="h-3.5 w-3.5 mr-1.5"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M12 5v14M5 12h14" />
                </svg>
                Add Goal
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Add Goal</DialogTitle>
                <DialogDescription>
                  Create a new goal for the orchestrator to work on.
                </DialogDescription>
              </DialogHeader>
              <div className="grid gap-4 py-4">
                <div className="grid gap-2">
                  <Label htmlFor="goal-title">Title</Label>
                  <Input
                    id="goal-title"
                    placeholder="Describe the goal..."
                    value={newTitle}
                    onChange={(e) => setNewTitle(e.target.value)}
                  />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="grid gap-2">
                    <Label>Priority</Label>
                    <Select value={newPriority} onValueChange={setNewPriority}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="critical">Critical</SelectItem>
                        <SelectItem value="high">High</SelectItem>
                        <SelectItem value="medium">Medium</SelectItem>
                        <SelectItem value="low">Low</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="grid gap-2">
                    <Label>Category</Label>
                    <Select value={newCategory} onValueChange={setNewCategory}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="feature">Feature</SelectItem>
                        <SelectItem value="bugfix">Bugfix</SelectItem>
                        <SelectItem value="refactor">Refactor</SelectItem>
                        <SelectItem value="infra">Infra</SelectItem>
                        <SelectItem value="docs">Docs</SelectItem>
                        <SelectItem value="test">Test</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </div>
              <DialogFooter>
                <Button
                  disabled={!newTitle.trim() || addMutation.isPending}
                  onClick={() => addMutation.mutate()}
                >
                  {addMutation.isPending ? "Adding..." : "Add Goal"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </CardHeader>
      <CardContent>
        {/* Inline quick-add */}
        <div className="flex gap-2 mb-4">
          <Input
            ref={quickAddRef}
            placeholder="Quick add goal... (Enter to submit)"
            value={quickAddValue}
            onChange={(e) => setQuickAddValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleQuickAdd();
              }
            }}
            disabled={quickAddding}
            className="h-8 text-sm"
          />
          <Button
            size="sm"
            variant="outline"
            className="h-8 shrink-0"
            disabled={!quickAddValue.trim() || quickAddding}
            onClick={handleQuickAdd}
          >
            {quickAddding ? (
              <svg className="animate-spin h-3.5 w-3.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
            ) : (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-3.5 w-3.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 5v14M5 12h14" />
              </svg>
            )}
          </Button>
        </div>

        <Tabs value={filter} onValueChange={setFilter} className="mb-4">
          <TabsList>
            {statusFilters.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>
                {tab.label}
                {tab.value === "all" && data ? ` (${data.total})` : ""}
                {tab.value === "pending" && data ? ` (${data.pending})` : ""}
                {tab.value === "completed" && data ? ` (${data.completed})` : ""}
                {tab.value === "failed" && data ? ` (${data.failed})` : ""}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        {isLoading && !data && (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-[52px]" />
            ))}
          </div>
        )}

        {error && !data && (
          <div className="flex items-center justify-center py-8">
            <p className="text-sm text-destructive">Failed to load goals</p>
          </div>
        )}

        {!isLoading && !error && goals.length === 0 && (
          <div className="flex items-center justify-center py-8">
            <p className="text-sm text-muted-foreground">No goals found</p>
          </div>
        )}

        {goals.length > 0 && (
          <div className="space-y-1.5">
            {goals.map((goal) => (
              <GoalRow
                key={goal.id}
                goal={goal}
                onDelete={(id) => deleteMutation.mutate(id)}
                isDeleting={deleteMutation.isPending}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function GoalRow({
  goal,
  onDelete,
  isDeleting,
}: {
  goal: Goal;
  onDelete: (id: string) => void;
  isDeleting: boolean;
}) {
  const isTemp = goal.id.startsWith("temp-");

  return (
    <div className={cn(
      "flex items-center gap-3 rounded-md px-3 py-2.5 hover:bg-accent/30 transition-colors border border-border/30 group",
      isTemp && "opacity-60 animate-pulse"
    )}>
      <span
        className={cn(
          "h-2 w-2 rounded-full shrink-0",
          goal.status === "completed" && "bg-emerald-400",
          goal.status === "failed" && "bg-destructive",
          goal.status === "pending" && "bg-muted-foreground",
          goal.status === "in_progress" && "bg-primary animate-pulse-dot"
        )}
      />
      <div className="min-w-0 flex-1">
        <p className={cn(
          "text-sm font-medium truncate",
          statusIcons[goal.status] ?? "text-foreground"
        )}>
          {goal.title}
        </p>
        {goal.result && (
          <p className="text-xs text-muted-foreground truncate mt-0.5">{goal.result}</p>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {/* Inline priority edit via dropdown */}
        <PriorityBadge goalId={goal.id} priority={goal.priority} disabled={isTemp} />
        <Badge variant="outline" className="text-[10px] h-5 px-1.5">
          {goal.category}
        </Badge>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
          disabled={isDeleting || isTemp}
          onClick={() => onDelete(goal.id)}
          aria-label={`Delete goal: ${goal.title}`}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="h-3.5 w-3.5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
          </svg>
        </Button>
      </div>
    </div>
  );
}

function PriorityBadge({
  goalId,
  priority,
  disabled,
}: {
  goalId: string;
  priority: string;
  disabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const [updating, setUpdating] = useState(false);

  const handlePriorityChange = async (newPriority: string) => {
    if (newPriority === priority || disabled) return;
    setUpdating(true);

    // Optimistic update
    queryClient.setQueryData(
      ["orch-goals", "all"],
      (old: { goals: Goal[]; total: number; pending: number; completed: number; failed: number } | undefined) => {
        if (!old) return old;
        return {
          ...old,
          goals: old.goals.map((g) =>
            g.id === goalId ? { ...g, priority: newPriority } : g
          ),
        };
      }
    );

    try {
      // The API doesn't have a dedicated updateGoalPriority endpoint,
      // so we delete and re-add. For now, just update the local cache
      // and invalidate on next refetch.
      queryClient.invalidateQueries({ queryKey: ["orch-goals"] });
    } finally {
      setUpdating(false);
    }
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={cn(
            "inline-flex items-center rounded-md border border-transparent px-1.5 h-5 text-[10px] font-semibold transition-colors cursor-pointer hover:ring-1 hover:ring-ring/30",
            priorityColors[priority] ?? "bg-muted text-muted-foreground",
            updating && "opacity-50",
            disabled && "pointer-events-none"
          )}
        >
          {priority}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-28">
        {priorityOptions.map((p) => (
          <DropdownMenuItem
            key={p}
            onClick={() => handlePriorityChange(p)}
            className="capitalize"
          >
            <span
              className={cn(
                "mr-2 h-2 w-2 rounded-full",
                p === "critical" && "bg-red-400",
                p === "high" && "bg-orange-400",
                p === "medium" && "bg-amber-400",
                p === "low" && "bg-blue-400"
              )}
            />
            {p}
            {p === priority && (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="ml-auto h-3.5 w-3.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <polyline points="20 6 9 17 4 12" />
              </svg>
            )}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
