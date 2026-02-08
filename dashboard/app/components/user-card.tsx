import { useState } from "react";
import type { User } from "~/lib/types";
import { Avatar, AvatarFallback } from "~/components/ui/avatar";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "~/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";

function relativeTime(dateStr: string): string {
  const now = Date.now();
  const date = new Date(dateStr).getTime();
  const diff = now - date;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

function getInitials(user: User): string {
  if (user.name) {
    return user.name
      .split(" ")
      .map((w) => w[0])
      .join("")
      .toUpperCase()
      .slice(0, 2);
  }
  return user.email.slice(0, 2).toUpperCase();
}

const roleBadgeVariant: Record<string, "default" | "secondary" | "outline"> = {
  owner: "default",
  moderator: "secondary",
  viewer: "outline",
};

const statusBadgeVariant: Record<string, "default" | "warning" | "destructive" | "success"> = {
  active: "success",
  pending_approval: "warning",
  suspended: "destructive",
  rejected: "destructive",
};

interface UserCardProps {
  user: User;
  currentUserId: string;
  onApprove?: (userId: string, role: string) => void;
  onReject?: (userId: string) => void;
  onRoleChange?: (userId: string, role: string) => void;
  onSuspend?: (userId: string) => void;
  onReactivate?: (userId: string) => void;
  onDelete?: (userId: string) => void;
  isLoading?: boolean;
}

export function UserCard({
  user,
  currentUserId,
  onApprove,
  onReject,
  onRoleChange,
  onSuspend,
  onReactivate,
  onDelete,
  isLoading,
}: UserCardProps) {
  const [approveRole, setApproveRole] = useState("viewer");
  const [approveOpen, setApproveOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const isSelf = user.id === currentUserId;
  const isOwner = user.role === "owner";

  return (
    <div className="flex items-center justify-between rounded-lg border border-border/50 bg-card/50 p-4">
      <div className="flex items-center gap-3">
        <Avatar className="h-10 w-10">
          <AvatarFallback className="text-sm bg-primary/10 text-primary">
            {getInitials(user)}
          </AvatarFallback>
        </Avatar>
        <div>
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium text-foreground">
              {user.name || user.email}
            </p>
            <Badge variant={roleBadgeVariant[user.role] ?? "outline"} className="text-[10px]">
              {user.role}
            </Badge>
            <Badge
              variant={statusBadgeVariant[user.status] ?? "outline"}
              className="text-[10px]"
            >
              {user.status.replace("_", " ")}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            {user.name ? user.email : ""}{user.name ? " · " : ""}Joined {relativeTime(user.created_at)}
          </p>
        </div>
      </div>

      {/* Actions — vary by status */}
      {!isSelf && !isOwner && (
        <div className="flex items-center gap-2">
          {/* Pending: Approve + Reject */}
          {user.status === "pending_approval" && (
            <>
              <Dialog open={approveOpen} onOpenChange={setApproveOpen}>
                <DialogTrigger asChild>
                  <Button size="sm" disabled={isLoading}>
                    Approve
                  </Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Approve {user.name || user.email}</DialogTitle>
                    <DialogDescription>
                      Choose the role to assign to this user.
                    </DialogDescription>
                  </DialogHeader>
                  <Select value={approveRole} onValueChange={setApproveRole}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="viewer">Viewer</SelectItem>
                      <SelectItem value="moderator">Moderator</SelectItem>
                    </SelectContent>
                  </Select>
                  <DialogFooter>
                    <Button
                      onClick={() => {
                        onApprove?.(user.id, approveRole);
                        setApproveOpen(false);
                      }}
                      disabled={isLoading}
                    >
                      Confirm
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => onReject?.(user.id)}
                disabled={isLoading}
              >
                Reject
              </Button>
            </>
          )}

          {/* Active: Role select + Suspend */}
          {user.status === "active" && (
            <>
              <Select
                value={user.role}
                onValueChange={(role) => onRoleChange?.(user.id, role)}
                disabled={isLoading}
              >
                <SelectTrigger className="w-[120px] h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="viewer">Viewer</SelectItem>
                  <SelectItem value="moderator">Moderator</SelectItem>
                </SelectContent>
              </Select>
              <Button
                size="sm"
                variant="outline"
                onClick={() => onSuspend?.(user.id)}
                disabled={isLoading}
              >
                Suspend
              </Button>
            </>
          )}

          {/* Suspended: Reactivate + Delete */}
          {user.status === "suspended" && (
            <>
              <Button
                size="sm"
                onClick={() => onReactivate?.(user.id)}
                disabled={isLoading}
              >
                Reactivate
              </Button>
              <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
                <DialogTrigger asChild>
                  <Button size="sm" variant="destructive" disabled={isLoading}>
                    Delete
                  </Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Delete {user.name || user.email}?</DialogTitle>
                    <DialogDescription>
                      This action cannot be undone. The user account will be
                      permanently deleted.
                    </DialogDescription>
                  </DialogHeader>
                  <DialogFooter>
                    <Button variant="outline" onClick={() => setDeleteOpen(false)}>
                      Cancel
                    </Button>
                    <Button
                      variant="destructive"
                      onClick={() => {
                        onDelete?.(user.id);
                        setDeleteOpen(false);
                      }}
                      disabled={isLoading}
                    >
                      Delete
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </>
          )}

          {/* Rejected: Delete */}
          {user.status === "rejected" && (
            <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
              <DialogTrigger asChild>
                <Button size="sm" variant="destructive" disabled={isLoading}>
                  Delete
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Delete {user.name || user.email}?</DialogTitle>
                  <DialogDescription>
                    This action cannot be undone.
                  </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  <Button variant="outline" onClick={() => setDeleteOpen(false)}>
                    Cancel
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={() => {
                      onDelete?.(user.id);
                      setDeleteOpen(false);
                    }}
                    disabled={isLoading}
                  >
                    Delete
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          )}
        </div>
      )}

      {/* Self indicator */}
      {isSelf && (
        <span className="text-xs text-muted-foreground italic">You</span>
      )}
    </div>
  );
}
