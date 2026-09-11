import { DetectorConfigPanel } from "@/components/dashboard/detector-config";
import { Overview } from "@/components/dashboard/overview";

export default function DashboardPage() {
  return (
    <div className="space-y-8">
      <Overview />
      <DetectorConfigPanel />
    </div>
  );
}
