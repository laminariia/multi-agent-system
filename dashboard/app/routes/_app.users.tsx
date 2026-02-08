import { useEffect } from "react";
import { useNavigate } from "@remix-run/react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Skeleton } from "~/components/ui/skeleton";
import { UserCard } from "~/components/user-card";
import { useAuthStore } from "~/stores/auth-store";
import { toast } from "~/hooks/use-toast";
import {
  fetchUsers,
  approveUser,
  rejectUser,
  updateUserRole,
  updateUserStatus,
  deleteUser,
  transferOwnership,
} from "~/lib/api";

function isAdmin(role?: string): boolean {
  return role === "owner" || role === "co_owner";
}

export default function UsersRoute() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);

  // Admin-only guard (owner or co_owner)
  useEffect(() => {
    if (user && !isAdmin(user.role)) {
      navigate("/dashboard", { replace: true });
    }
  }, [user, navigate]);

  // Queries per tab
  const pending = useQuery({
    queryKey: ["users", "pending_approval"],
    queryFn: () => fetchUsers({ status: "pending_approval" }),
    enabled: isAdmin(user?.role),
  });

  const active = useQuery({
    queryKey: ["users", "active"],
    queryFn: () => fetchUsers({ status: "active" }),
    enabled: isAdmin(user?.role),
  });

  const suspended = useQuery({
    queryKey: ["users", "suspended"],
    queryFn: () => fetchUsers({ status: "suspended" }),
    enabled: isAdmin(user?.role),
  });

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["users"] });
    queryClient.invalidateQueries({ queryKey: ["pending-users-count"] });
  };

  // Mutations
  const approveMut = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) =>
      approveUser(id, role),
    onSuccess: () => {
      toast({ title: "User approved" });
      invalidateAll();
    },
    onError: (e) => toast({ title: "Error", description: String(e) }),
  });

  const rejectMut = useMutation({
    mutationFn: (id: string) => rejectUser(id),
    onSuccess: () => {
      toast({ title: "User rejected" });
      invalidateAll();
    },
    onError: (e) => toast({ title: "Error", description: String(e) }),
  });

  const roleMut = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) =>
      updateUserRole(id, role),
    onSuccess: () => {
      toast({ title: "Role updated" });
      invalidateAll();
    },
    onError: (e) => toast({ title: "Error", description: String(e) }),
  });

  const suspendMut = useMutation({
    mutationFn: (id: string) => updateUserStatus(id, "suspended"),
    onSuccess: () => {
      toast({ title: "User suspended" });
      invalidateAll();
    },
    onError: (e) => toast({ title: "Error", description: String(e) }),
  });

  const reactivateMut = useMutation({
    mutationFn: (id: string) => updateUserStatus(id, "active"),
    onSuccess: () => {
      toast({ title: "User reactivated" });
      invalidateAll();
    },
    onError: (e) => toast({ title: "Error", description: String(e) }),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteUser(id),
    onSuccess: () => {
      toast({ title: "User deleted" });
      invalidateAll();
    },
    onError: (e) => toast({ title: "Error", description: String(e) }),
  });

  const transferMut = useMutation({
    mutationFn: (id: string) => transferOwnership(id),
    onSuccess: () => {
      toast({ title: "Ownership transferred" });
      // Update local user state to co_owner
      if (user) {
        setUser({ ...user, role: "co_owner" });
      }
      invalidateAll();
    },
    onError: (e) => toast({ title: "Error", description: String(e) }),
  });

  const isAnyLoading =
    approveMut.isPending ||
    rejectMut.isPending ||
    roleMut.isPending ||
    suspendMut.isPending ||
    reactivateMut.isPending ||
    deleteMut.isPending ||
    transferMut.isPending;

  if (!user || !isAdmin(user.role)) {
    return null;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Users</h1>
        <p className="text-sm text-muted-foreground">
          Manage user accounts, approve registrations, and assign roles.
        </p>
      </div>

      <Tabs defaultValue="pending">
        <TabsList>
          <TabsTrigger value="pending">
            Pending
            {(pending.data?.total ?? 0) > 0 && (
              <span className="ml-1.5 flex h-5 min-w-[20px] items-center justify-center rounded-full bg-primary px-1.5 text-[10px] font-bold text-primary-foreground">
                {pending.data?.total}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="active">
            Active ({active.data?.total ?? 0})
          </TabsTrigger>
          <TabsTrigger value="suspended">
            Suspended ({suspended.data?.total ?? 0})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="pending" className="space-y-3">
          {pending.isLoading ? (
            <LoadingSkeleton />
          ) : (pending.data?.users.length ?? 0) === 0 ? (
            <EmptyState message="No pending registrations" />
          ) : (
            pending.data?.users.map((u) => (
              <UserCard
                key={u.id}
                user={u}
                currentUserId={user.id}
                currentUserRole={user.role}
                onApprove={(id, role) => approveMut.mutate({ id, role })}
                onReject={(id) => rejectMut.mutate(id)}
                isLoading={isAnyLoading}
              />
            ))
          )}
        </TabsContent>

        <TabsContent value="active" className="space-y-3">
          {active.isLoading ? (
            <LoadingSkeleton />
          ) : (active.data?.users.length ?? 0) === 0 ? (
            <EmptyState message="No active users" />
          ) : (
            active.data?.users.map((u) => (
              <UserCard
                key={u.id}
                user={u}
                currentUserId={user.id}
                currentUserRole={user.role}
                onRoleChange={(id, role) => roleMut.mutate({ id, role })}
                onSuspend={(id) => suspendMut.mutate(id)}
                onTransfer={(id) => transferMut.mutate(id)}
                isLoading={isAnyLoading}
              />
            ))
          )}
        </TabsContent>

        <TabsContent value="suspended" className="space-y-3">
          {suspended.isLoading ? (
            <LoadingSkeleton />
          ) : (suspended.data?.users.length ?? 0) === 0 ? (
            <EmptyState message="No suspended users" />
          ) : (
            suspended.data?.users.map((u) => (
              <UserCard
                key={u.id}
                user={u}
                currentUserId={user.id}
                currentUserRole={user.role}
                onReactivate={(id) => reactivateMut.mutate(id)}
                onDelete={(id) => deleteMut.mutate(id)}
                isLoading={isAnyLoading}
              />
            ))
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      {[1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-[72px] rounded-lg" />
      ))}
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex h-32 items-center justify-center rounded-lg border border-dashed border-border/50">
      <p className="text-sm text-muted-foreground">{message}</p>
    </div>
  );
}
