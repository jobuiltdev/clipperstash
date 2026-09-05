"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { CLIP_STATE_LABEL } from "@/components/dashboard/clip-state";
import { MomentList } from "@/components/dashboard/moment-list";
import {
  EmptyState,
  ErrorNotice,
  Fact,
  LoadingLines,
  Panel,
  RefreshButton,
  SessionStatusBadge,
  StatCard,
} from "@/components/dashboard/ui";
import { getDetectorConfig, getSession, getSessionMoments } from "@/lib/api";
import { formatCount, formatElapsed, formatTimestamp } from "@/lib/format";
import { useResource } from "@/lib/use-resource";

import type { ClipState } from "@/lib/api";

const FILTERS: { value: ClipState | "all"; label: string }[] = [
  { value: "all", label: "All moments" },
  { value: "not_requested", label: CLIP_STATE_LABEL.not_requested },
  { value: "requested", label: CLIP_STATE_LABEL.requested },
  { value: "request_unknown", label: CLIP_STATE_LABEL.request_unknown },
  { value: "created", label: CLIP_STATE_LABEL.created },
  { value: "failed", label: CLIP_STATE_LABEL.failed },
];

/**
 * One broadcast, and the moments detected during it.
 *
 * Chat appears only as a total. The transcript is pipeline input, not
 * something the dashboard publishes, and the backend does not serve it.
 */
export function SessionDetail({ sessionId }: { sessionId: number }) {
  const [filter, setFilter] = useState<ClipState | "all">("all");

  const loadSession = useCallback(() => getSession(sessionId), [sessionId]);
  const loadMoments = useCallback(
    () => getSessionMoments(sessionId, filter === "all" ? {} : { clipState: filter }),
    [sessionId, filter],
  );

  const session = useResource(loadSession);
  const moments = useResource(loadMoments);
  const config = useResource(getDetectorConfig);

  const threshold =
    config.state.kind === "ready" ? config.state.data.candidate_threshold : null;

  const refreshAll = useCallback(() => {
    session.reload();
    moments.reload();
  }, [session, moments]);

  return (
    <div className="space-y-6">
      <Panel
        headingId="session-heading"
        title="Session"
        actions={
          <RefreshButton onClick={refreshAll} busy={session.reloading || moments.reloading} />
        }
      >
        {session.state.kind === "loading" && <LoadingLines rows={3} label="Loading session" />}

        {session.state.kind === "failed" && (
          <ErrorNotice message={session.state.message} onRetry={session.reload} />
        )}

        {session.state.kind === "ready" && (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="text-lg font-semibold">
                {session.state.data.streamer.display_name || session.state.data.streamer.username}
              </h3>
              <SessionStatusBadge status={session.state.data.status} />
              <Link
                href={`/dashboard/streamers/${session.state.data.streamer.id}`}
                className="text-sm underline underline-offset-2"
              >
                All sessions for this streamer
              </Link>
              <a
                href={session.state.data.streamer.channel_url}
                target="_blank"
                rel="noreferrer noopener"
                className="text-sm underline underline-offset-2"
              >
                View on Twitch
              </a>
            </div>

            {session.state.data.title && <p className="text-sm">{session.state.data.title}</p>}

            <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Fact label="Started">
                <time dateTime={session.state.data.started_at}>
                  {formatTimestamp(session.state.data.started_at)}
                </time>
              </Fact>
              <Fact label={session.state.data.status === "live" ? "Live for" : "Lasted"}>
                {formatElapsed(session.state.data.started_at, session.state.data.ended_at)}
              </Fact>
              <Fact label="Category">{session.state.data.category || "—"}</Fact>
              <Fact label="Viewers at last check">
                {formatCount(session.state.data.viewer_count)}
              </Fact>
              <Fact label="Last observed">
                <time dateTime={session.state.data.last_observed_at}>
                  {formatTimestamp(session.state.data.last_observed_at)}
                </time>
              </Fact>
              <Fact label="Ended">
                {session.state.data.ended_at ? (
                  <time dateTime={session.state.data.ended_at}>
                    {formatTimestamp(session.state.data.ended_at)}
                  </time>
                ) : (
                  "Not yet observed as ended"
                )}
              </Fact>
              <Fact label="Language">{session.state.data.language || "—"}</Fact>
              <Fact label="Twitch stream id">
                <code className="text-xs">{session.state.data.platform_stream_id}</code>
              </Fact>
            </dl>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                label="Chat messages"
                value={formatCount(session.state.data.counts.chat_messages)}
                hint="Counted, never quoted."
              />
              <StatCard
                label="Moments"
                value={formatCount(session.state.data.counts.total)}
              />
              <StatCard
                label="Clips ready"
                value={formatCount(session.state.data.counts.created)}
                tone={session.state.data.counts.created > 0 ? "positive" : "neutral"}
              />
              <StatCard
                label="Outcome unknown"
                value={formatCount(session.state.data.counts.request_unknown)}
                tone={session.state.data.counts.request_unknown > 0 ? "caution" : "neutral"}
              />
            </div>
          </div>
        )}
      </Panel>

      <Panel
        headingId="session-moments-heading"
        title="Moments"
        description="Newest first. Scores are shown against the detector's current threshold."
        actions={
          <div className="flex items-center gap-2">
            <label htmlFor="clip-state-filter" className="text-sm text-muted">
              Show
            </label>
            <select
              id="clip-state-filter"
              value={filter}
              onChange={(event) => setFilter(event.target.value as ClipState | "all")}
              className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            >
              {FILTERS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        }
      >
        {moments.state.kind === "loading" && <LoadingLines rows={4} label="Loading moments" />}

        {moments.state.kind === "failed" && (
          <ErrorNotice message={moments.state.message} onRetry={moments.reload} />
        )}

        {moments.state.kind === "ready" &&
          (moments.state.data.results.length === 0 ? (
            <EmptyState
              title={filter === "all" ? "No moments in this session" : "Nothing in that state"}
              detail={
                filter === "all"
                  ? "The detector records a candidate only when chat is unusually active for this channel. A quiet session produces none, which is the expected result rather than a fault."
                  : "No moment in this session is in that state. Choose “All moments” to see the rest."
              }
            />
          ) : (
            <>
              <MomentList moments={moments.state.data.results} threshold={threshold} />
              <p className="text-sm text-muted">
                Showing {formatCount(moments.state.data.results.length)} of{" "}
                {formatCount(moments.state.data.count)}.
                {moments.state.data.has_more && " More remain beyond this page."}
              </p>
            </>
          ))}
      </Panel>
    </div>
  );
}
