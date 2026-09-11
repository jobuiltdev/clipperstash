import type { ReactNode } from "react";

/**
 * Status vocabulary.
 *
 * Five tones and no more. The product has no decorative colour, so a coloured
 * thing on screen is always reporting state, and the same tone always means the
 * same thing:
 *
 * - `live`    something is happening, or a clip exists
 * - `pending` in flight; the outcome is expected but not in yet
 * - `unknown` nobody knows the outcome — deliberately not `failed`
 * - `failed`  it definitively did not work
 * - `neutral` no state to report
 *
 * Every badge carries text as well as colour, so none of this is load-bearing
 * for a reader who cannot distinguish them.
 */

export type Tone = "neutral" | "live" | "pending" | "unknown" | "failed";

const DOT: Record<Tone, string> = {
  neutral: "bg-faint",
  live: "bg-live",
  pending: "bg-pending",
  unknown: "bg-unknown",
  failed: "bg-failed",
};

const BADGE: Record<Tone, string> = {
  neutral: "border-border text-muted",
  live: "border-live/35 bg-live/10 text-live",
  pending: "border-pending/35 bg-pending/10 text-pending",
  unknown: "border-unknown/35 bg-unknown/10 text-unknown",
  failed: "border-failed/35 bg-failed/10 text-failed",
};

const TEXT: Record<Tone, string> = {
  neutral: "text-muted",
  live: "text-live",
  pending: "text-pending",
  unknown: "text-unknown",
  failed: "text-failed",
};

export function toneText(tone: Tone): string {
  return TEXT[tone];
}

export function StatusDot({
  tone,
  pulse = false,
}: {
  tone: Tone;
  /** Reserved for genuinely ongoing state. Stilled by reduced-motion. */
  pulse?: boolean;
}) {
  return (
    <span
      aria-hidden
      className={`size-1.5 shrink-0 rounded-full ${DOT[tone]} ${pulse ? "animate-live" : ""}`}
    />
  );
}

export function Badge({
  tone = "neutral",
  dot = false,
  pulse = false,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  pulse?: boolean;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-medium ${BADGE[tone]}`}
    >
      {dot && <StatusDot tone={tone} pulse={pulse} />}
      {children}
    </span>
  );
}

/** A quieter label for metadata that is not a state: a category, a language. */
export function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center whitespace-nowrap rounded-md border border-border px-2 py-0.5 text-xs text-muted">
      {children}
    </span>
  );
}
