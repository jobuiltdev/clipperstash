import Link from "next/link";

import { ClipStateBadge } from "@/components/dashboard/clip-state";
import { formatCount, formatRatio, formatScore, formatTimestamp } from "@/lib/format";

import type { MomentFeedItem, MomentSummary } from "@/lib/api";

/**
 * A list of moments.
 *
 * Each row carries enough to judge it without opening it — when, how strong,
 * what happened to the clip — and links to the detail view for the full
 * working. The rows are plain links: nothing on this page can request a clip.
 */

function hasContext(moment: MomentSummary | MomentFeedItem): moment is MomentFeedItem {
  return "streamer" in moment;
}

export function MomentRow({
  moment,
  threshold,
}: {
  moment: MomentSummary | MomentFeedItem;
  threshold: number | null;
}) {
  const strong = threshold !== null && moment.total_score >= threshold;

  return (
    <li>
      <Link
        href={`/dashboard/moments/${moment.id}`}
        className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-md border border-border bg-background p-3 hover:border-foreground/30"
      >
        <span
          className={`w-14 shrink-0 text-lg font-semibold tabular-nums ${strong ? "text-emerald-500" : ""}`}
        >
          {formatScore(moment.total_score)}
        </span>

        <span className="min-w-0 flex-1 space-y-0.5">
          <span className="block text-sm">
            <time dateTime={moment.detected_at}>{formatTimestamp(moment.detected_at)}</time>
          </span>
          {hasContext(moment) && (
            <span className="block truncate text-xs text-muted">
              {moment.streamer.display_name || moment.streamer.username}
              {moment.session_title ? ` · ${moment.session_title}` : ""}
            </span>
          )}
          <span className="block text-xs text-muted">
            {formatCount(moment.current_message_count)} messages ·{" "}
            {formatCount(moment.current_unique_chatter_count)} chatters ·{" "}
            {formatRatio(moment.velocity_ratio)} baseline
          </span>
        </span>

        <ClipStateBadge state={moment.clip_state} />
      </Link>
    </li>
  );
}

export function MomentList({
  moments,
  threshold,
}: {
  moments: (MomentSummary | MomentFeedItem)[];
  threshold: number | null;
}) {
  return (
    <ul className="space-y-2">
      {moments.map((moment) => (
        <MomentRow key={moment.id} moment={moment} threshold={threshold} />
      ))}
    </ul>
  );
}
