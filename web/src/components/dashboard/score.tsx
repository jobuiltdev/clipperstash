import { formatComponent, formatScore } from "@/lib/format";

import type { MomentSummary } from "@/lib/api";

/**
 * The score, and why it came out that way.
 *
 * A bare 0–100 number is not reviewable: two moments can share a total and be
 * completely different events. Every view that shows a score therefore shows the
 * components it was built from and the threshold it was measured against.
 */

export function ScoreDial({
  score,
  threshold,
}: {
  score: number;
  threshold: number | null;
}) {
  const above = threshold !== null && score >= threshold;
  const fraction = Math.max(0, Math.min(1, score / 100));

  return (
    <div className="flex items-center gap-4">
      <div>
        <span
          className={`text-4xl font-semibold tabular-nums tracking-tight ${
            above ? "text-live" : "text-foreground"
          }`}
        >
          {formatScore(score)}
        </span>
        <span className="ml-1 text-base text-faint">/100</span>
      </div>

      {threshold !== null && (
        <div className="min-w-0 flex-1 space-y-1.5">
          {/* The bar carries the threshold as a tick, so "how far over the line"
              is legible without reading two numbers and subtracting. */}
          <div className="relative h-1.5 overflow-hidden rounded-full bg-raised">
            <div
              className={`h-full rounded-full ${above ? "bg-live" : "bg-muted"}`}
              style={{ width: `${fraction * 100}%` }}
            />
            <span
              aria-hidden
              className="absolute inset-y-0 w-px bg-border-strong"
              style={{ left: `${threshold}%` }}
            />
          </div>
          <p className="text-xs text-muted">
            Threshold {formatScore(threshold)}
            {above ? " · cleared" : " · not reached"}
          </p>
        </div>
      )}
    </div>
  );
}

export function ComponentBar({
  label,
  value,
  weight,
}: {
  label: string;
  value: number;
  weight?: number;
}) {
  const percent = Math.max(0, Math.min(1, value)) * 100;

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm text-foreground">{label}</span>
        <span className="text-sm tabular-nums text-muted">{formatComponent(value)}</span>
      </div>
      <div
        role="meter"
        aria-label={label}
        aria-valuenow={Math.round(percent)}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-1.5 overflow-hidden rounded-full bg-raised"
      >
        <div
          className="h-full rounded-full bg-foreground/70 transition-[width] duration-300"
          style={{ width: `${percent}%` }}
        />
      </div>
      {weight !== undefined && (
        <p className="text-xs text-faint">Weight {formatComponent(weight)}</p>
      )}
    </div>
  );
}

const COMPONENTS: { key: keyof MomentSummary; label: string; weight: string }[] = [
  { key: "velocity_score", label: "Velocity", weight: "velocity" },
  { key: "reaction_score", label: "Reaction", weight: "reaction" },
  { key: "diversity_score", label: "Diversity", weight: "diversity" },
  { key: "emote_score", label: "Emote", weight: "emote" },
  { key: "absolute_activity_score", label: "Activity", weight: "absolute_activity" },
];

export function ScoreBreakdown({
  moment,
  weights,
}: {
  moment: MomentSummary;
  weights?: Record<string, number>;
}) {
  return (
    <div className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
      {COMPONENTS.map(({ key, label, weight }) => (
        <ComponentBar
          key={key}
          label={label}
          value={moment[key] as number}
          weight={weights?.[weight]}
        />
      ))}
    </div>
  );
}
