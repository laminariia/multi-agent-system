import { RunnerStatusCard } from "~/components/runner-status-card";
import { GoalList } from "~/components/goal-list";
import { HealthGauge } from "~/components/health-gauge";
import { MilestoneTimeline } from "~/components/milestone-timeline";
import { RunnerLogViewer } from "~/components/runner-log-viewer";

export default function OrchestratorPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Orchestrator</h1>

      {/* Runner status */}
      <RunnerStatusCard />

      {/* Goals + Health side by side */}
      <div className="grid gap-6 lg:grid-cols-2">
        <GoalList />
        <HealthGauge />
      </div>

      {/* Milestones full width */}
      <MilestoneTimeline />

      {/* Logs full width */}
      <RunnerLogViewer />
    </div>
  );
}
