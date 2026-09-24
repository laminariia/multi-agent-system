import { useState } from "react";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Badge } from "~/components/ui/badge";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
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

const CATEGORY_TABS = [
  { value: "", label: "All" },
  { value: "web_dev", label: "Web Dev" },
  { value: "design", label: "Design" },
  { value: "marketing", label: "Marketing" },
  { value: "copywriting", label: "Copywriting" },
  { value: "mobile", label: "Mobile" },
  { value: "general", label: "General" },
];

const AVATAR_COLORS: Record<string, string> = {
  web_dev: "bg-green-500",
  design: "bg-orange-500",
  marketing: "bg-purple-500",
  mobile: "bg-blue-500",
  copywriting: "bg-pink-500",
  general: "bg-zinc-500",
};

const AVATAR_INITIALS: Record<string, string> = {
  web_dev: "WD",
  design: "D",
  marketing: "MK",
  mobile: "MB",
  copywriting: "CW",
  general: "G",
};

function getAvatarColor(category: string | null): string {
  return AVATAR_COLORS[category ?? "general"] ?? "bg-zinc-600";
}

function getAvatarInitials(category: string | null, username: string): string {
  if (category && AVATAR_INITIALS[category]) return AVATAR_INITIALS[category];
  return username.slice(0, 2).toUpperCase();
}

function ChannelRow({
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
  const avatarColor = getAvatarColor(channel.category);
  const initials = getAvatarInitials(channel.category, channel.username);

  return (
    <div className="flex items-center gap-4 py-4 px-2 border-b border-zinc-800 last:border-0">
      {/* Avatar circle */}
      <div
        className={`h-10 w-10 shrink-0 rounded-full ${avatarColor} flex items-center justify-center text-white text-xs font-bold`}
      >
        {initials}
      </div>

      {/* Channel info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-semibold text-white text-sm">
            @{channel.username}
          </span>
          {channel.category && (
            <Badge className="bg-zinc-800 text-zinc-300 text-[10px]">
              {channel.category.replace("_", " ")}
            </Badge>
          )}
          {channel.active ? (
            <Badge className="bg-green-500/20 text-green-400 text-[10px]">
              Active
            </Badge>
          ) : (
            <Badge className="bg-orange-500/20 text-orange-400 text-[10px]">
              Paused
            </Badge>
          )}
        </div>
        {channel.title && (
          <p className="text-xs text-zinc-500 mt-0.5 truncate">{channel.title}</p>
        )}
      </div>

      {/* Actions */}
      {isAdmin && (
        <div className="flex items-center gap-1.5 shrink-0">
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs border-zinc-700 text-zinc-300"
            disabled={isToggling}
            onClick={onToggle}
          >
            {channel.active ? "Pause" : "Enable"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-xs text-zinc-500 hover:text-destructive"
            onClick={onDelete}
          >
            Delete
          </Button>
        </div>
      )}
    </div>
  );
}

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
    onError: (e) =>
      toast({ title: "Error", description: String(e), variant: "destructive" }),
  });

  const canSubmit = username.trim().length > 0 && !createMut.isPending;

  return (
    <Dialog open onOpenChange={onClose}>
      <DialogContent className="bg-zinc-900 border-zinc-800 text-white sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>Add Telegram Channel</DialogTitle>
          <DialogDescription className="text-zinc-500">
            The listener will start monitoring this channel for job postings
            within 5 minutes.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label htmlFor="tg-username" className="text-zinc-400 text-xs">
              Channel username
            </Label>
            <Input
              id="tg-username"
              placeholder="@freelance_jobs"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="bg-zinc-800 border-zinc-700"
            />
            <p className="text-[11px] text-zinc-500">
              With or without @. The channel must be public.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="tg-title" className="text-zinc-400 text-xs">
              Title (optional)
            </Label>
            <Input
              id="tg-title"
              placeholder="Freelance Web Dev Jobs"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="bg-zinc-800 border-zinc-700"
            />
          </div>

          <div className="space-y-2">
            <Label className="text-zinc-400 text-xs">Category (optional)</Label>
            <div className="flex gap-2 flex-wrap">
              {CATEGORY_TABS.filter((c) => c.value).map((cat) => (
                <button
                  key={cat.value}
                  type="button"
                  onClick={() =>
                    setCategory(category === cat.value ? "" : cat.value)
                  }
                  className={`px-3 py-1 text-xs font-medium rounded-full border transition-colors ${
                    category === cat.value
                      ? "bg-orange-500 text-white border-orange-500"
                      : "border-zinc-700 text-zinc-400 hover:border-zinc-600"
                  }`}
                >
                  {cat.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} className="border-zinc-700">
            Cancel
          </Button>
          <Button
            disabled={!canSubmit}
            onClick={() => createMut.mutate()}
            className="bg-orange-500 hover:bg-orange-600 text-white"
          >
            {createMut.isPending ? "Adding..." : "Add Channel"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function TelegramChannelsRoute() {
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "owner" || user?.role === "co_owner";

  const [showAdd, setShowAdd] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<TelegramChannel | null>(
    null
  );
  const [activeCategory, setActiveCategory] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["telegram-channels"],
    queryFn: () => fetchTelegramChannels(),
    refetchInterval: 60_000,
  });

  const channels = data?.channels ?? [];

  // Count per category for tab badges
  const countByCategory = (cat: string) =>
    cat ? channels.filter((c) => c.category === cat).length : channels.length;

  const filtered = activeCategory
    ? channels.filter((c) => c.category === activeCategory)
    : channels;

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["telegram-channels"] });

  const toggleMut = useMutation({
    mutationFn: (ch: TelegramChannel) =>
      updateTelegramChannel(ch.id, { active: !ch.active }),
    onSuccess: (updated) => {
      toast({ title: updated.active ? "Channel enabled" : "Channel disabled" });
      invalidate();
    },
    onError: (e) =>
      toast({ title: "Error", description: String(e), variant: "destructive" }),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteTelegramChannel(id),
    onSuccess: () => {
      toast({ title: "Channel deleted" });
      setConfirmDelete(null);
      invalidate();
    },
    onError: (e) =>
      toast({ title: "Error", description: String(e), variant: "destructive" }),
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-white">
            Telegram Channels
          </h1>
          <Badge className="bg-zinc-800 text-zinc-300 text-xs">
            {channels.length} channels ({channels.filter((c) => c.active).length} active)
          </Badge>
        </div>
        {isAdmin && (
          <Button
            size="sm"
            onClick={() => setShowAdd(true)}
            className="bg-orange-500 hover:bg-orange-600 text-white"
          >
            Add Channel
          </Button>
        )}
      </div>

      {/* Category tabs */}
      <Tabs
        value={activeCategory}
        onValueChange={setActiveCategory}
      >
        <TabsList className="bg-zinc-900 border border-zinc-800">
          {CATEGORY_TABS.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.label}
              {!isLoading && (
                <span className="ml-1.5 text-[10px] text-zinc-500">
                  {countByCategory(tab.value)}
                </span>
              )}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {/* Channel list */}
      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-16 rounded-lg" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-zinc-800">
          <div className="text-center">
            <p className="text-sm text-zinc-500">
              {channels.length === 0
                ? "No channels configured yet"
                : "No channels in this category"}
            </p>
            {channels.length === 0 && isAdmin && (
              <Button
                variant="link"
                size="sm"
                className="mt-2 text-orange-500"
                onClick={() => setShowAdd(true)}
              >
                Add your first channel
              </Button>
            )}
          </div>
        </div>
      ) : (
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg px-4">
          {filtered.map((ch) => (
            <ChannelRow
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
          <DialogContent className="bg-zinc-900 border-zinc-800 text-white sm:max-w-[400px]">
            <DialogHeader>
              <DialogTitle>Delete channel</DialogTitle>
              <DialogDescription className="text-zinc-500">
                Remove <strong>@{confirmDelete.username}</strong> from
                monitoring? The listener will stop checking this channel on its
                next refresh cycle.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                variant="outline"
                className="border-zinc-700"
                onClick={() => setConfirmDelete(null)}
              >
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
