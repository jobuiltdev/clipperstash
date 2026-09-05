import Link from "next/link";
import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Dashboard · ClipperStash",
  description: "Inspect observed streams, detected moments and clip outcomes.",
};

/**
 * The dashboard shell.
 *
 * Read-only throughout: there is no control here that changes pipeline state,
 * and no automatic refresh. Every page under this layout fetches when it is
 * opened and again when a person asks.
 */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-8 px-6 py-12">
      <header className="space-y-2">
        <nav aria-label="Breadcrumb" className="text-sm text-muted">
          <Link href="/" className="underline underline-offset-2">
            ClipperStash
          </Link>
          <span aria-hidden> / </span>
          <Link href="/dashboard" className="underline underline-offset-2">
            Dashboard
          </Link>
        </nav>
        <p className="text-sm text-muted">
          Observation only. Nothing here contacts Twitch, runs the detector or requests a clip.
        </p>
      </header>

      <main className="flex-1">{children}</main>
    </div>
  );
}
