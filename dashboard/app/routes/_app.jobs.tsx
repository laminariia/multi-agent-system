import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent } from "~/components/ui/card";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { JobCard } from "~/components/job-card";
import { fetchJobs } from "~/lib/api";

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

export default function JobsPage() {
  const [statusFilter, setStatusFilter] = useState("all");
  const [platformFilter, setPlatformFilter] = useState("all");

  const { data, isLoading, error } = useQuery({
    queryKey: ["jobs", statusFilter, platformFilter],
    queryFn: () =>
      fetchJobs({
        status: statusFilter === "all" ? undefined : statusFilter,
        platform: platformFilter === "all" ? undefined : platformFilter,
        limit: 50,
      }),
    refetchInterval: 30_000,
  });

  const jobs = data?.jobs ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Jobs</h1>
          {total > 0 && (
            <p className="text-sm text-muted-foreground mt-1">
              {total} jobs found
            </p>
          )}
        </div>

        <Select value={platformFilter} onValueChange={setPlatformFilter}>
          <SelectTrigger className="w-[160px]">
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
      </div>

      {/* Status filter tabs */}
      <Tabs value={statusFilter} onValueChange={setStatusFilter}>
        <TabsList>
          {statusFilters.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {/* Loading */}
      {isLoading && (
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
              Scout agent will discover jobs automatically
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
    </div>
  );
}
