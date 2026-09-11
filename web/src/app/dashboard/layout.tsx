import type { Metadata } from "next";
import type { ReactNode } from "react";

import { PageContainer } from "@/components/ui/page";

export const metadata: Metadata = {
  title: "Dashboard",
  description: "Inspect observed streams, detected moments and clip outcomes.",
};

/**
 * The dashboard area.
 *
 * Read-only throughout: no control under this layout changes pipeline state,
 * and nothing refreshes on a timer. Each page fetches when it is opened and
 * again when a person asks.
 */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return <PageContainer>{children}</PageContainer>;
}
