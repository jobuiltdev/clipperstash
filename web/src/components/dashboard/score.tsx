import { formatComponent, formatScore } from "@/lib/format";

import type { MomentSummary } from "@/lib/api";

/**
 * The score, and why it came out that way.
 *
 * A bare 0–100 number is not reviewable: two moments can share a total and be
 * completely different events. Every view that shows a score therefore shows
 * the components it was built from, and the threshold it was measured against.
 */

export function ScoreDial({
  score,
  threshold,
}: {
  score: number;
  threshold: number | null;
}) {
  const above = threshold !== null && score >= threshold;
  return (
    <div className="flex items-baseline gap-2">
      <span
        className={`text-3xl font-semibold tabular-nums ${above ? "text-emerald-500" : ""}`}
      >
        {formatScore(score)}
      </span>
      {threshold !== null && (
        <span className="text-sm text-muted">
          / threshold {formatScore(threshold)}
        </span>
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
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-3 text-xs">
        <span>
          {label}
          {weight !== undefined && (
            <span className="text-muted"> · weight {formatComponent(weight)}</span>
          )}
        </span>
        <span className="tabular-nums text-muted">{formatComponent(value)}</span>
      </div>
      <div
        role="meter"
        aria-label={label}
        aria-valuenow={Math.round(percent)}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-1.5 overflow-hidden rounded-full bg-border"
      >
        <div className="h-full rounded-full bg-foreground/60" style={{ width: `${percent}%` }} />
      </div>
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
    <div className="grid gap-3 sm:grid-cols-2">
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
