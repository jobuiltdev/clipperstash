import Link from "next/link";

import { ClipStateBadge } from "@/components/dashboard/clip-state";
import { formatCount, formatRatio, formatScore, formatTime, formatTimestamp } from "@/lib/format";

import type { MomentFeedItem, MomentSummary } from "@/lib/api";

/**
 * A list of moments.
 *
 * Each row carries enough to judge it without opening it — when, how strong,
 * what happened to the clip — and links to the detail view for the full working.
 * The rows are plain links: nothing on this page can request a clip.
 *
 * `timeline` draws the rows as a chronological run with a rail down the left,
 * which is how a session's moments are read: in order, relative to each other.
 * Away from a session — on the overview, where rows come from different
 * streamers — the rail would imply a sequence that does not exist, so the same
 * rows are drawn as a plain list.
 */

function hasContext(moment: MomentSummary | MomentFeedItem): moment is MomentFeedItem {
  return "streamer" in moment;
}

function Metrics({ moment }: { moment: MomentSummary | MomentFeedItem }) {
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
      <span className="tabular-nums">{formatCount(moment.current_message_count)} messages</span>
      <span aria-hidden className="text-border-strong">
        ·
      </span>
      <span className="tabular-nums">
        {formatCount(moment.current_unique_chatter_count)} chatters
      </span>
      <span aria-hidden className="text-border-strong">
        ·
      </span>
      <span className="tabular-nums">{formatRatio(moment.velocity_ratio)} baseline</span>
    </span>
  );
}

export function MomentRow({
  moment,
  threshold,
  timeline = false,
}: {
  moment: MomentSummary | MomentFeedItem;
  threshold: number | null;
  timeline?: boolean;
}) {
  const strong = threshold !== null && moment.total_score >= threshold;

  return (
    <li className={timeline ? "relative pl-8" : ""}>
      {timeline && (
        <span
          aria-hidden
          className={`absolute left-[11px] top-6 size-[7px] -translate-x-1/2 rounded-full ring-4 ring-background ${
            strong ? "bg-live" : "bg-border-strong"
          }`}
        />
      )}

      <Link
        href={`/dashboard/moments/${moment.id}`}
        className="flex flex-wrap items-center gap-x-4 gap-y-3 rounded-lg border border-border bg-surface px-4 py-3.5 transition-colors duration-150 hover:border-border-strong hover:bg-raised"
      >
        <span className="flex w-14 shrink-0 flex-col">
          <span
            className={`text-xl font-semibold tabular-nums leading-none ${
              strong ? "text-live" : "text-foreground"
            }`}
          >
            {formatScore(moment.total_score)}
          </span>
          <span className="mt-1 text-[0.625rem] uppercase tracking-wider text-faint">
            score
          </span>
        </span>

        <span className="min-w-0 flex-1 space-y-1">
          <span className="block text-sm font-medium text-foreground">
            <time dateTime={moment.detected_at}>
              {timeline ? formatTime(moment.detected_at) : formatTimestamp(moment.detected_at)}
            </time>
          </span>
          {hasContext(moment) && (
            <span className="block truncate text-xs text-muted">
              {moment.streamer.display_name || moment.streamer.username}
              {moment.session_title ? ` · ${moment.session_title}` : ""}
            </span>
          )}
          <Metrics moment={moment} />
        </span>

        <ClipStateBadge state={moment.clip_state} />
      </Link>
    </li>
  );
}

export function MomentList({
  moments,
  threshold,
  timeline = false,
}: {
  moments: (MomentSummary | MomentFeedItem)[];
  threshold: number | null;
  timeline?: boolean;
}) {
  return (
    <ul
      className={
        timeline
          ? "relative space-y-2 before:absolute before:inset-y-3 before:left-[11px] before:w-px before:bg-border"
          : "space-y-2"
      }
    >
      {moments.map((moment) => (
        <MomentRow
          key={moment.id}
          moment={moment}
          threshold={threshold}
          timeline={timeline}
        />
      ))}
    </ul>
  );
}
