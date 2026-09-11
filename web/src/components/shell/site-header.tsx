"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Brand } from "@/components/shell/brand";

/**
 * The product's navigation.
 *
 * Two destinations, because the product has two: a place to connect Twitch and
 * resolve a channel, and a place to look at what the pipeline recorded. Nothing
 * here links to a section that does not exist — an empty nav slot promising a
 * feature is worse than a short nav.
 */
const LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/", label: "Setup" },
] as const;

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

export function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-6 px-4 sm:px-6">
        <Link
          href="/dashboard"
          className="shrink-0 rounded-md text-foreground transition-opacity hover:opacity-80"
        >
          <Brand />
          <span className="sr-only">ClipperStash home</span>
        </Link>

        <nav aria-label="Primary" className="min-w-0">
          <ul className="flex items-center gap-1">
            {LINKS.map((link) => {
              const active = isActive(pathname, link.href);
              return (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    aria-current={active ? "page" : undefined}
                    className={`relative flex h-14 items-center px-3 text-sm transition-colors duration-150 ${
                      active
                        ? "font-medium text-foreground"
                        : "text-muted hover:text-foreground"
                    }`}
                  >
                    {link.label}
                    {/* The active marker sits on the header's own bottom edge,
                        so the current section reads at a glance. */}
                    {active && (
                      <span
                        aria-hidden
                        className="absolute inset-x-3 bottom-0 h-px bg-foreground"
                      />
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>
    </header>
  );
}
