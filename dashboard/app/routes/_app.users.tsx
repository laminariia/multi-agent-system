import { useEffect, useState, useMemo } from "react";
import { useNavigate } from "@remix-run/react";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Pagination } from "~/components/pagination";
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

const PAGE_SIZE = 10;

export default function UsersRoute() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [pendingPage, setPendingPage] = useState(0);
  const [activePage, setActivePage] = useState(0);
  const [suspendedPage, setSuspendedPage] = useState(0);

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      // Reset pages on search change
      setPendingPage(0);
      setActivePage(0);
      setSuspendedPage(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

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

  // Client-side filtering by search
  const filterUsers = useMemo(() => {
    const q = debouncedSearch.toLowerCase();
    if (!q) return (users: typeof pending.data) => users?.users ?? [];
    return (users: typeof pending.data) =>
      (users?.users ?? []).filter(
        (u) =>
          (u.name?.toLowerCase().includes(q) ?? false) ||
          u.email.toLowerCase().includes(q)
      );
  }, [debouncedSearch]);

  const filteredPending = filterUsers(pending.data);
  const filteredActive = filterUsers(active.data);
  const filteredSuspended = filterUsers(suspended.data);

  // Paginated slices
  const paginatedPending = filteredPending.slice(
    pendingPage * PAGE_SIZE,
    (pendingPage + 1) * PAGE_SIZE
  );
  const paginatedActive = filteredActive.slice(
    activePage * PAGE_SIZE,
    (activePage + 1) * PAGE_SIZE
  );
  const paginatedSuspended = filteredSuspended.slice(
    suspendedPage * PAGE_SIZE,
    (suspendedPage + 1) * PAGE_SIZE
  );

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

      {/* Search */}
      <div className="relative">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="11" cy="11" r="8" />
          <path d="m21 21-4.3-4.3" />
        </svg>
        <Input
          placeholder="Search by name or email..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-9 pr-9 h-9"
        />
        {search && (
          <button
            type="button"
            className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
            onClick={() => setSearch("")}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-4 w-4"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        )}
      </div>

      <Tabs defaultValue="pending">
        <TabsList>
          <TabsTrigger value="pending">
            Pending
            {filteredPending.length > 0 && (
              <span className="ml-1.5 flex h-5 min-w-[20px] items-center justify-center rounded-full bg-primary px-1.5 text-[10px] font-bold text-primary-foreground">
                {filteredPending.length}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="active">
            Active ({filteredActive.length})
          </TabsTrigger>
          <TabsTrigger value="suspended">
            Suspended ({filteredSuspended.length})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="pending" className="space-y-3">
          {pending.isLoading ? (
            <LoadingSkeleton />
          ) : filteredPending.length === 0 ? (
            <EmptyState message={debouncedSearch ? "No matching users" : "No pending registrations"} />
          ) : (
            <>
              {paginatedPending.map((u) => (
                <UserCard
                  key={u.id}
                  user={u}
                  currentUserId={user.id}
                  currentUserRole={user.role}
                  onApprove={(id, role) => approveMut.mutate({ id, role })}
                  onReject={(id) => rejectMut.mutate(id)}
                  isLoading={isAnyLoading}
                />
              ))}
              <Pagination
                page={pendingPage}
                pageSize={PAGE_SIZE}
                total={filteredPending.length}
                onPageChange={setPendingPage}
                className="mt-4"
              />
            </>
          )}
        </TabsContent>

        <TabsContent value="active" className="space-y-3">
          {active.isLoading ? (
            <LoadingSkeleton />
          ) : filteredActive.length === 0 ? (
            <EmptyState message={debouncedSearch ? "No matching users" : "No active users"} />
          ) : (
            <>
              {paginatedActive.map((u) => (
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
              ))}
              <Pagination
                page={activePage}
                pageSize={PAGE_SIZE}
                total={filteredActive.length}
                onPageChange={setActivePage}
                className="mt-4"
              />
            </>
          )}
        </TabsContent>

        <TabsContent value="suspended" className="space-y-3">
          {suspended.isLoading ? (
            <LoadingSkeleton />
          ) : filteredSuspended.length === 0 ? (
            <EmptyState message={debouncedSearch ? "No matching users" : "No suspended users"} />
          ) : (
            <>
              {paginatedSuspended.map((u) => (
                <UserCard
                  key={u.id}
                  user={u}
                  currentUserId={user.id}
                  currentUserRole={user.role}
                  onReactivate={(id) => reactivateMut.mutate(id)}
                  onDelete={(id) => deleteMut.mutate(id)}
                  isLoading={isAnyLoading}
                />
              ))}
              <Pagination
                page={suspendedPage}
                pageSize={PAGE_SIZE}
                total={filteredSuspended.length}
                onPageChange={setSuspendedPage}
                className="mt-4"
              />
            </>
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
