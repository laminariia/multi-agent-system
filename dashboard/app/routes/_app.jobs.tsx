import { useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
export { RouteErrorBoundary as ErrorBoundary } from "~/components/route-error-boundary";
import { Card, CardContent } from "~/components/ui/card";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { JobCard } from "~/components/job-card";
import { Pagination } from "~/components/pagination";
import { fetchJobs, startJobScan } from "~/lib/api";
import { downloadCSV } from "~/lib/utils";
import { toast } from "~/hooks/use-toast";

const PAGE_SIZE = 24;

const statusFilters = [
  { value: "all", label: "All" },
  { value: "discovered", label: "New" },
  { value: "qualified", label: "Qualified" },
  { value: "bid_sent", label: "Bid Sent" },
  { value: "in_progress", label: "In Progress" },
  { value: "completed", label: "Completed" },
  { value: "disqualified", label: "Disqualified" },
] as const;

const platformOptions = [
  { value: "all", label: "All Platforms" },
  { value: "freelancer", label: "Freelancer" },
  { value: "upwork", label: "Upwork" },
  { value: "fl_ru", label: "FL.ru" },
  { value: "kwork", label: "Kwork" },
] as const;

const sortOptions = [
  { value: "newest", label: "Newest" },
  { value: "oldest", label: "Oldest" },
  { value: "score_high", label: "Score: High" },
  { value: "score_low", label: "Score: Low" },
  { value: "budget_high", label: "Budget: High" },
  { value: "budget_low", label: "Budget: Low" },
] as const;

export default function JobsPage() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("all");
  const [platformFilter, setPlatformFilter] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortOrder, setSortOrder] = useState("newest");
  const [page, setPage] = useState(0);
  const [scanning, setScanning] = useState(false);

  // Reset page on filter change
  const handleStatusChange = (v: string) => { setStatusFilter(v); setPage(0); };
  const handlePlatformChange = (v: string) => { setPlatformFilter(v); setPage(0); };

  // Debounce search query
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(searchQuery), 300);
    return () => clearTimeout(id);
  }, [searchQuery]);

  const handleSearchChange = (v: string) => {
    setSearchQuery(v);
    setPage(0);
  };

  const { data, isLoading, error } = useQuery({
    queryKey: ["jobs", statusFilter, platformFilter, debouncedSearch, sortOrder, page],
    queryFn: () =>
      fetchJobs({
        status: statusFilter === "all" ? undefined : statusFilter,
        platform: platformFilter === "all" ? undefined : platformFilter,
        search: debouncedSearch || undefined,
        sort: sortOrder === "newest" ? undefined : sortOrder,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  const handleStartScan = async () => {
    setScanning(true);
    try {
      const platform = platformFilter === "all" ? undefined : platformFilter;
      const res = await startJobScan(platform);
      toast({
        title: "Scout scan started",
        description: `Scanning ${res.platform === "all" ? "all platforms" : res.platform}. New jobs will appear shortly.`,
        variant: "success",
      });
      // Refetch jobs after a short delay to pick up new results
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ["jobs"] });
      }, 5000);
    } catch (err) {
      toast({
        title: "Failed to start scan",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setScanning(false);
    }
  };

  const jobs = data?.jobs ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Jobs</h1>
          {total > 0 && (
            <p className="text-sm text-muted-foreground mt-1">
              {total} jobs found
            </p>
          )}
        </div>

        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 w-full sm:w-auto">
          <Button
            variant="default"
            size="sm"
            disabled={scanning}
            onClick={handleStartScan}
          >
            {scanning ? (
              <>
                <svg className="animate-spin -ml-1 mr-2 h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                Scanning...
              </>
            ) : (
              <>
                <svg className="mr-2 h-4 w-4" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="11" cy="11" r="8" />
                  <path d="m21 21-4.3-4.3" />
                </svg>
                Scan for Jobs
              </>
            )}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={jobs.length === 0}
            onClick={() => {
              const rows = jobs.map((j) => ({
                title: j.title,
                platform: j.platform,
                status: j.status,
                score:
                  j.score != null ? Math.round(j.score * 100) + "%" : "",
                budget_min: j.budget_min ?? "",
                budget_max: j.budget_max ?? "",
                currency: j.currency,
                discovered_at: j.discovered_at,
                url: j.url ?? "",
              }));
              downloadCSV(
                rows,
                `jobs-${new Date().toISOString().slice(0, 10)}.csv`
              );
            }}
          >
            Export CSV
          </Button>
          <Input
            placeholder="Search jobs..."
            value={searchQuery}
            onChange={(e) => handleSearchChange(e.target.value)}
            className="w-full sm:w-[200px]"
          />
          <Select value={platformFilter} onValueChange={handlePlatformChange}>
            <SelectTrigger className="w-full sm:w-[160px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {platformOptions.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={sortOrder} onValueChange={(v) => { setSortOrder(v); setPage(0); }}>
            <SelectTrigger className="w-full sm:w-[140px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {sortOptions.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Status filter tabs */}
      <Tabs value={statusFilter} onValueChange={handleStatusChange}>
        <TabsList>
          {statusFilters.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {/* Loading */}
      {isLoading && !data && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-[160px]" />
          ))}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load jobs:{" "}
          {error instanceof Error ? error.message : "Unknown error"}
        </div>
      )}

      {/* Empty */}
      {!isLoading && !error && jobs.length === 0 && (
        <Card className="border-border/50">
          <CardContent className="flex flex-col items-center justify-center py-16">
            <svg
              className="h-12 w-12 text-muted-foreground/30 mb-4"
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2 2v2m4 6h.01M5 20h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
            <p className="text-muted-foreground text-lg font-medium">
              No jobs found
            </p>
            <p className="text-muted-foreground/70 text-sm mt-1">
              Click "Scan for Jobs" or wait for the Scout agent
            </p>
          </CardContent>
        </Card>
      )}

      {/* Job grid */}
      {jobs.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {jobs.map((job) => (
            <JobCard key={job.id} job={job} />
          ))}
        </div>
      )}

      {/* Pagination */}
      <Pagination
        page={page}
        pageSize={PAGE_SIZE}
        total={total}
        onPageChange={setPage}
      />
    </div>
  );
}
