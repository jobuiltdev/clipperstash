import type { Metadata } from "next";
import type { ReactNode } from "react";

import { SiteHeader } from "@/components/shell/site-header";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "ClipperStash",
    template: "%s · ClipperStash",
  },
  description: "Turn live moments into clips automatically.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col bg-background text-foreground">
        {/* First tab stop on every page, for anyone navigating by keyboard. */}
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-foreground focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-inverse"
        >
          Skip to content
        </a>

        <SiteHeader />

        <main id="main" className="flex-1">
          {children}
        </main>

        <footer className="border-t border-border">
          <div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6">
            <p className="text-xs text-faint">
              Every stage runs when you start it. Nothing here monitors, detects or clips on
              its own.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
