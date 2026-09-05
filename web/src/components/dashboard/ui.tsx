import type { ReactNode } from "react";

/**
 * The small shared pieces every dashboard view is built from.
 *
 * Loading, empty and failure are given the same weight as the success case
 * here, because on a real installation they are just as common: a session with
 * no moments yet is normal, and a backend that cannot be reached must say so
 * rather than render as "nothing happened".
 */

export function Panel({
  title,
  description,
  actions,
  children,
  headingId,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  headingId: string;
}) {
  return (
    <section
      aria-labelledby={headingId}
      className="space-y-4 rounded-lg border border-border bg-surface p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id={headingId} className="text-sm font-medium">
            {title}
          </h2>
          {description && <p className="text-sm text-muted">{description}</p>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function RefreshButton({
  onClick,
  busy,
  label = "Refresh",
}: {
  onClick: () => void;
  busy: boolean;
  label?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className="rounded-md border border-border px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"
    >
      {busy ? "Refreshing…" : label}
    </button>
  );
}

/** A placeholder with the shape of the content it stands in for. */
export function LoadingLines({ rows = 3, label }: { rows?: number; label: string }) {
  return (
    <div role="status" aria-live="polite" className="space-y-2">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          aria-hidden
          className="h-10 animate-pulse rounded-md border border-border bg-background"
        />
      ))}
    </div>
  );
}

export function ErrorNotice({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="space-y-3 rounded-md border border-rose-500/40 bg-background p-4"
    >
      <p className="text-sm text-rose-500">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="rounded-md border border-border px-3 py-1.5 text-sm font-medium"
        >
          Try again
        </button>
      )}
    </div>
  );
}

/**
 * Nothing here yet — which is a state, not a problem.
 *
 * Says why it is empty, so an operator can tell "not observed yet" from
 * "observed and quiet".
 */
export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="rounded-md border border-dashed border-border bg-background p-6 text-center">
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 text-sm text-muted">{detail}</p>
    </div>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "neutral" | "positive" | "caution" | "negative";
}) {
  const toneClass = {
    neutral: "",
    positive: "text-emerald-500",
    caution: "text-amber-500",
    negative: "text-rose-500",
  }[tone];

  return (
    <div className="rounded-md border border-border bg-background p-4">
      <p className="text-xs uppercase tracking-wide text-muted">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${toneClass}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}

/** A label/value pair, as used throughout the detail views. */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs uppercase tracking-wide text-muted">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

export function SessionStatusBadge({ status }: { status: "live" | "ended" }) {
  const live = status === "live";
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-xs">
      <span
        aria-hidden
        className={`size-1.5 rounded-full ${live ? "bg-emerald-500" : "bg-muted"}`}
      />
      {live ? "Live" : "Ended"}
    </span>
  );
}
