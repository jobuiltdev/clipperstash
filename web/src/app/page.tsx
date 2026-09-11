import type { Metadata } from "next";

import { SetupWorkspace } from "@/components/setup/setup-workspace";
import { PageContainer } from "@/components/ui/page";

export const metadata: Metadata = {
  // Spelled out rather than relying on the root template: Next applies a
  // parent `title.template` to child segments, and this page is the root
  // segment itself, so it would otherwise render as a bare "Setup".
  title: "Setup · ClipperStash",
  description: "Connect Twitch and resolve the channel ClipperStash should watch.",
};

export default function SetupPage() {
  return (
    <PageContainer>
      <SetupWorkspace />
    </PageContainer>
  );
}
