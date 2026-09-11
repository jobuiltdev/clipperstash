import type { ReactNode } from "react";

import { type Tone, toneText } from "@/components/ui/status";

/**
 * The two ways numbers appear in the product: as a headline figure, and as a
 * labelled fact in a list. Both set figures in tabular numerals so a column of
 * them lines up and a changing value does not shift the layout.
 */

export function Stat({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: ReactNode;
  /** Colour only when the number itself is reporting state. */
  tone?: Tone;
}) {
  return (
    <div className="rounded-lg border border-border bg-raised px-4 py-3.5">
      <p className="text-[0.6875rem] font-medium uppercase tracking-wider text-faint">
        {label}
      </p>
      <p
        className={`mt-1.5 text-2xl font-semibold tabular-nums ${
          tone === "neutral" ? "text-foreground" : toneText(tone)
        }`}
      >
        {value}
      </p>
      {hint && <p className="mt-1 text-xs leading-relaxed text-muted">{hint}</p>}
    </div>
  );
}

export function StatGrid({
  children,
  columns = 3,
}: {
  children: ReactNode;
  columns?: 2 | 3 | 4;
}) {
  const layout = {
    2: "sm:grid-cols-2",
    3: "sm:grid-cols-2 lg:grid-cols-3",
    4: "sm:grid-cols-2 lg:grid-cols-4",
  }[columns];

  return <div className={`grid grid-cols-1 gap-3 ${layout}`}>{children}</div>;
}

/** A labelled value inside a `<dl>`. */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 space-y-1">
      <dt className="text-[0.6875rem] font-medium uppercase tracking-wider text-faint">
        {label}
      </dt>
      <dd className="break-words text-sm text-foreground">{children}</dd>
    </div>
  );
}

export function FactGrid({
  children,
  columns = 4,
}: {
  children: ReactNode;
  columns?: 2 | 3 | 4;
}) {
  const layout = {
    2: "sm:grid-cols-2",
    3: "sm:grid-cols-2 lg:grid-cols-3",
    4: "sm:grid-cols-2 lg:grid-cols-4",
  }[columns];

  return <dl className={`grid grid-cols-1 gap-x-6 gap-y-4 ${layout}`}>{children}</dl>;
}

/** The dash used wherever a value is genuinely absent. */
export function Absent() {
  return <span className="text-faint">—</span>;
}
