import Link from "next/link";

import { Badge, Tag } from "@/components/ui/status";
import { formatCount, formatElapsed, formatTimestamp } from "@/lib/format";

import type { SessionSummary } from "@/lib/api";

/**
 * A list of stream sessions.
 *
 * `ended_at` is when ClipperStash *observed* the broadcast to be over, not a
 * timestamp Twitch supplies, so a live session's elapsed time is shown running
 * and an ended one is shown as measured.
 */

export function SessionStatusBadge({ status }: { status: "live" | "ended" }) {
  const live = status === "live";
  return (
    <Badge tone={live ? "live" : "neutral"} dot pulse={live}>
      {live ? "Live" : "Ended"}
    </Badge>
  );
}

export function SessionRow({
  session,
  showStreamer = true,
}: {
  session: SessionSummary;
  showStreamer?: boolean;
}) {
  return (
    <li>
      <Link
        href={`/dashboard/sessions/${session.id}`}
        className="block rounded-lg border border-border bg-surface px-4 py-3.5 transition-colors duration-150 hover:border-border-strong hover:bg-raised"
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {showStreamer && (
            <span className="font-medium text-foreground">
              {session.streamer.display_name || session.streamer.username}
            </span>
          )}
          <SessionStatusBadge status={session.status} />
          {session.category && <Tag>{session.category}</Tag>}
        </div>

        {session.title && (
          <p className="mt-2 truncate text-sm text-muted">{session.title}</p>
        )}

        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
          <span>
            <time dateTime={session.started_at}>{formatTimestamp(session.started_at)}</time>
          </span>
          <span aria-hidden className="text-border-strong">
            ·
          </span>
          <span className="tabular-nums">
            {formatElapsed(session.started_at, session.ended_at)}{" "}
            {session.status === "live" ? "so far" : "long"}
          </span>
          <span aria-hidden className="text-border-strong">
            ·
          </span>
          <span className="tabular-nums">{formatCount(session.moment_count)} moments</span>
          <span aria-hidden className="text-border-strong">
            ·
          </span>
          <span
            className={`tabular-nums ${session.clip_created_count > 0 ? "text-live" : ""}`}
          >
            {formatCount(session.clip_created_count)} clips
          </span>
        </div>
      </Link>
    </li>
  );
}

export function SessionList({
  sessions,
  showStreamer = true,
}: {
  sessions: SessionSummary[];
  showStreamer?: boolean;
}) {
  return (
    <ul className="space-y-2">
      {sessions.map((session) => (
        <SessionRow key={session.id} session={session} showStreamer={showStreamer} />
      ))}
    </ul>
  );
}
