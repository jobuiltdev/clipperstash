import Link from "next/link";

import { SessionStatusBadge } from "@/components/dashboard/ui";
import { formatCount, formatElapsed, formatTimestamp } from "@/lib/format";

import type { SessionSummary } from "@/lib/api";

/**
 * A list of stream sessions.
 *
 * `ended_at` is when ClipperStash *observed* the broadcast to be over, not a
 * timestamp Twitch supplies, so a live session's elapsed time is shown running
 * and an ended one is shown as measured.
 */

export function SessionRow({ session }: { session: SessionSummary }) {
  return (
    <li>
      <Link
        href={`/dashboard/sessions/${session.id}`}
        className="block space-y-2 rounded-md border border-border bg-background p-3 hover:border-foreground/30"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">
            {session.streamer.display_name || session.streamer.username}
          </span>
          <SessionStatusBadge status={session.status} />
          {session.category && (
            <span className="rounded-full border border-border px-2 py-0.5 text-xs text-muted">
              {session.category}
            </span>
          )}
        </div>

        {session.title && <p className="truncate text-sm">{session.title}</p>}

        <p className="text-xs text-muted">
          Started{" "}
          <time dateTime={session.started_at}>{formatTimestamp(session.started_at)}</time> ·{" "}
          {formatElapsed(session.started_at, session.ended_at)}{" "}
          {session.status === "live" ? "so far" : "long"} ·{" "}
          {formatCount(session.moment_count)} moments ·{" "}
          {formatCount(session.clip_created_count)} clips
        </p>
      </Link>
    </li>
  );
}

export function SessionList({ sessions }: { sessions: SessionSummary[] }) {
  return (
    <ul className="space-y-2">
      {sessions.map((session) => (
        <SessionRow key={session.id} session={session} />
      ))}
    </ul>
  );
}
