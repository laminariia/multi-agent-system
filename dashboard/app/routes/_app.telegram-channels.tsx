import { useState } from "react";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent } from "~/components/ui/card";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Badge } from "~/components/ui/badge";
import { Skeleton } from "~/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "~/components/ui/dialog";
import { useAuthStore } from "~/stores/auth-store";
import { toast } from "~/hooks/use-toast";
import {
  fetchTelegramChannels,
  createTelegramChannel,
  updateTelegramChannel,
  deleteTelegramChannel,
} from "~/lib/api";
import type { TelegramChannel } from "~/lib/types";

const CATEGORIES = [
  { value: "", label: "All categories" },
  { value: "web_dev", label: "Web Dev" },
  { value: "mobile", label: "Mobile" },
  { value: "design", label: "Design" },
  { value: "copywriting", label: "Copywriting" },
  { value: "marketing", label: "Marketing" },
  { value: "other", label: "Other" },
];

export default function TelegramChannelsRoute() {
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "owner" || user?.role === "co_owner";

  const [showAdd, setShowAdd] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<TelegramChannel | null>(null);
  const [filterCategory, setFilterCategory] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["telegram-channels"],
    queryFn: () => fetchTelegramChannels(),
  });

  const channels = data?.channels ?? [];
  const filtered = filterCategory
    ? channels.filter((c) => c.category === filterCategory)
    : channels;
  const activeCount = channels.filter((c) => c.active).length;

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["telegram-channels"] });

  const toggleMut = useMutation({
    mutationFn: (ch: TelegramChannel) => updateTelegramChannel(ch.id, { active: !ch.active }),
    onSuccess: (updated) => {
      toast({ title: updated.active ? "Channel enabled" : "Channel disabled" });
      invalidate();
    },
    onError: (e) => toast({ title: "Error", description: String(e), variant: "destructive" }),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteTelegramChannel(id),
    onSuccess: () => {
      toast({ title: "Channel deleted" });
      setConfirmDelete(null);
      invalidate();
    },
    onError: (e) => toast({ title: "Error", description: String(e), variant: "destructive" }),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Telegram Channels</h1>
          <p className="text-sm text-muted-foreground">
            Manage Telegram channels monitored for freelance job postings.
            {!isLoading && (
              <span className="ml-1">
                {activeCount} active / {channels.length} total
              </span>
            )}
          </p>
        </div>
        {isAdmin && (
          <Button size="sm" onClick={() => setShowAdd(true)}>
            <svg xmlns="http://www.w3.org/2000/svg" className="mr-1.5 h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            Add Channel
          </Button>
        )}
      </div>

      {/* Category filter */}
      <div className="flex gap-2 flex-wrap">
        {CATEGORIES.map((cat) => (
          <button
            key={cat.value}
            type="button"
            onClick={() => setFilterCategory(cat.value)}
            className={`px-3 py-1 text-xs font-medium rounded-full border transition-colors ${
              filterCategory === cat.value
                ? "bg-primary text-primary-foreground border-primary"
                : "border-border text-muted-foreground hover:bg-accent"
            }`}
          >
            {cat.label}
          </button>
        ))}
      </div>

      {/* Channel list */}
      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-[72px] rounded-lg" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-border/50">
          <div className="text-center">
            <p className="text-sm text-muted-foreground">
              {channels.length === 0
                ? "No channels configured yet"
                : "No channels match the filter"}
            </p>
            {channels.length === 0 && isAdmin && (
              <Button variant="link" size="sm" className="mt-2" onClick={() => setShowAdd(true)}>
                Add your first channel
              </Button>
            )}
          </div>
        </div>
      ) : (
        <div className="grid gap-3">
          {filtered.map((ch) => (
            <ChannelCard
              key={ch.id}
              channel={ch}
              isAdmin={isAdmin}
              onToggle={() => toggleMut.mutate(ch)}
              onDelete={() => setConfirmDelete(ch)}
              isToggling={toggleMut.isPending}
            />
          ))}
        </div>
      )}

      {/* Add dialog */}
      {showAdd && (
        <AddChannelDialog
          onClose={() => setShowAdd(false)}
          onSuccess={invalidate}
        />
      )}

      {/* Delete confirmation */}
      {confirmDelete && (
        <Dialog open onOpenChange={() => setConfirmDelete(null)}>
          <DialogContent className="sm:max-w-[400px]">
            <DialogHeader>
              <DialogTitle>Delete channel</DialogTitle>
              <DialogDescription>
                Remove <strong>@{confirmDelete.username}</strong> from monitoring?
                The listener will stop checking this channel on its next refresh cycle.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirmDelete(null)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                disabled={deleteMut.isPending}
                onClick={() => deleteMut.mutate(confirmDelete.id)}
              >
                {deleteMut.isPending ? "Deleting..." : "Delete"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Channel card
// ---------------------------------------------------------------------------

function ChannelCard({
  channel,
  isAdmin,
  onToggle,
  onDelete,
  isToggling,
}: {
  channel: TelegramChannel;
  isAdmin: boolean;
  onToggle: () => void;
  onDelete: () => void;
  isToggling: boolean;
}) {
  return (
    <Card className={!channel.active ? "opacity-60" : undefined}>
      <CardContent className="flex items-center justify-between py-4 px-5">
        <div className="flex items-center gap-3 min-w-0">
          {/* Telegram icon */}
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-sky-500/10">
            <svg viewBox="0 0 24 24" className="h-4.5 w-4.5 text-sky-500" fill="currentColor">
              <path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z" />
            </svg>
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium text-sm truncate">@{channel.username}</span>
              {channel.active ? (
                <Badge variant="success" className="text-[10px]">Active</Badge>
              ) : (
                <Badge variant="outline" className="text-[10px]">Paused</Badge>
              )}
              {channel.category && (
                <Badge variant="secondary" className="text-[10px]">{channel.category}</Badge>
              )}
            </div>
            {channel.title && (
              <p className="text-xs text-muted-foreground truncate mt-0.5">{channel.title}</p>
            )}
          </div>
        </div>

        {isAdmin && (
          <div className="flex items-center gap-1.5 shrink-0 ml-3">
            <Button
              variant="ghost"
              size="sm"
              className="h-8 text-xs"
              disabled={isToggling}
              onClick={onToggle}
            >
              {channel.active ? "Pause" : "Enable"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 text-xs text-destructive hover:text-destructive"
              onClick={onDelete}
            >
              Delete
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Add channel dialog
// ---------------------------------------------------------------------------

function AddChannelDialog({
  onClose,
  onSuccess,
}: {
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [username, setUsername] = useState("");
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("");

  const createMut = useMutation({
    mutationFn: () =>
      createTelegramChannel({
        username: username.trim(),
        title: title.trim() || undefined,
        category: category || undefined,
      }),
    onSuccess: () => {
      toast({ title: "Channel added" });
      onSuccess();
      onClose();
    },
    onError: (e) => toast({ title: "Error", description: String(e), variant: "destructive" }),
  });

  const canSubmit = username.trim().length > 0 && !createMut.isPending;

  return (
    <Dialog open onOpenChange={onClose}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>Add Telegram Channel</DialogTitle>
          <DialogDescription>
            The listener will start monitoring this channel for job postings
            within 5 minutes.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label htmlFor="tg-username">Channel username</Label>
            <Input
              id="tg-username"
              placeholder="@freelance_jobs"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
            <p className="text-[11px] text-muted-foreground">
              With or without @. The channel must be public.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="tg-title">Title (optional)</Label>
            <Input
              id="tg-title"
              placeholder="Freelance Web Dev Jobs"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label>Category (optional)</Label>
            <div className="flex gap-2 flex-wrap">
              {CATEGORIES.filter((c) => c.value).map((cat) => (
                <button
                  key={cat.value}
                  type="button"
                  onClick={() => setCategory(category === cat.value ? "" : cat.value)}
                  className={`px-3 py-1 text-xs font-medium rounded-full border transition-colors ${
                    category === cat.value
                      ? "bg-primary text-primary-foreground border-primary"
                      : "border-border text-muted-foreground hover:bg-accent"
                  }`}
                >
                  {cat.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!canSubmit} onClick={() => createMut.mutate()}>
            {createMut.isPending ? "Adding..." : "Add Channel"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
