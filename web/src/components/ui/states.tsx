import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

/**
 * Loading, empty and failure, treated as real states rather than as gaps.
 *
 * On a working installation these are what a reader sees most: a session with
 * no moments yet is the normal case, and a backend that cannot be reached has
 * to say so rather than render as "nothing happened". Each of these says what
 * is true and, where there is one, what to do about it.
 */

/** A placeholder with the shape of the rows it stands in for. */
export function Skeleton({
  rows = 3,
  label,
  height = "h-16",
}: {
  rows?: number;
  label: string;
  height?: string;
}) {
  return (
    <div role="status" aria-live="polite" className="space-y-2">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          aria-hidden
          className={`${height} animate-pulse rounded-lg border border-border bg-raised`}
        />
      ))}
    </div>
  );
}

/**
 * Nothing here yet — and why.
 *
 * The reason matters: "no moments" could mean the detector has not been run, or
 * that it ran and the chat was quiet. Those call for different actions, so the
 * copy always says which.
 */
export function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-border px-6 py-10 text-center">
      <p className="text-sm font-medium text-foreground">{title}</p>
      <p className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-muted">{detail}</p>
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

/**
 * Something went wrong, in the backend's own words.
 *
 * The message shown is always the user-safe one the API sent; internals, stack
 * traces and provider responses never reach here.
 */
export function ErrorState({
  message,
  onRetry,
  title = "Could not load this",
}: {
  message: string;
  onRetry?: () => void;
  title?: string;
}) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-failed/30 bg-failed/5 px-5 py-4"
    >
      <p className="text-sm font-medium text-failed">{title}</p>
      <p className="mt-1 max-w-prose text-sm leading-relaxed text-muted">{message}</p>
      {onRetry && (
        <Button size="sm" onClick={onRetry} className="mt-3">
          Try again
        </Button>
      )}
    </div>
  );
}
