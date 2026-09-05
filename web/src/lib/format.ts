/**
 * Presentation helpers.
 *
 * Timestamps arrive from the backend as ISO-8601 in UTC. They are rendered in
 * the reader's own locale and zone by the browser, and never reformatted by
 * hand — an operator comparing a dashboard row against a Twitch VOD needs the
 * time their machine shows them.
 */

const MISSING = "—";

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return MISSING;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return MISSING;
  }
  return parsed.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function formatTime(value: string | null | undefined): string {
  if (!value) {
    return MISSING;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return MISSING;
  }
  return parsed.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** A machine-readable timestamp for `<time dateTime>`, or undefined. */
export function isoOrUndefined(value: string | null | undefined): string | undefined {
  return value ?? undefined;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) {
    return MISSING;
  }
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${remainder}s`;
}

/** Elapsed time between two instants, or between one instant and now. */
export function formatElapsed(from: string, to: string | null): string {
  const start = new Date(from).getTime();
  const end = to ? new Date(to).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) {
    return MISSING;
  }
  return formatDuration((end - start) / 1000);
}

export function formatScore(score: number): string {
  return score.toFixed(1);
}

/** A 0–1 component score as a percentage, for a bar's width and its label. */
export function formatComponent(score: number): string {
  return `${Math.round(score * 100)}%`;
}

export function formatRatio(ratio: number): string {
  return `${ratio.toFixed(2)}×`;
}

export function formatCount(value: number): string {
  return value.toLocaleString();
}
