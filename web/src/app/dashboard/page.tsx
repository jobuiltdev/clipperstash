import { DetectorConfigPanel } from "@/components/dashboard/detector-config";
import { Overview } from "@/components/dashboard/overview";

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-semibold tracking-tight">Dashboard</h1>
      <Overview />
      <DetectorConfigPanel />
    </div>
  );
}
