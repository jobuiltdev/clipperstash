"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { CLIP_STATE_LABEL, CLIP_STATE_TONE } from "@/components/dashboard/clip-state";
import { MomentList } from "@/components/dashboard/moment-list";
import { SessionStatusBadge } from "@/components/dashboard/session-list";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Absent, Fact, FactGrid, Stat, StatGrid } from "@/components/ui/data";
import { Breadcrumbs, PageHeader } from "@/components/ui/page";
import { Tag, toneText } from "@/components/ui/status";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { getDetectorConfig, getSession, getSessionMoments } from "@/lib/api";
import { formatCount, formatElapsed, formatTimestamp } from "@/lib/format";
import { useResource } from "@/lib/use-resource";

import type { ClipState, SessionDetail as SessionDetailData } from "@/lib/api";

const FILTERS: { value: ClipState | "all"; label: string }[] = [
  { value: "all", label: "All moments" },
  { value: "created", label: CLIP_STATE_LABEL.created },
  { value: "requested", label: CLIP_STATE_LABEL.requested },
  { value: "request_unknown", label: CLIP_STATE_LABEL.request_unknown },
  { value: "failed", label: CLIP_STATE_LABEL.failed },
  { value: "not_requested", label: CLIP_STATE_LABEL.not_requested },
];

/**
 * One broadcast, and the moments detected during it.
 *
 * The moments are the point of this page, so they are given a timeline: read in
 * order, they show how a session actually went — a quiet hour, three bursts in
 * ten minutes, a clip that never confirmed.
 *
 * Chat appears only as a total. The transcript is pipeline input, not something
 * the dashboard publishes, and the backend does not serve it.
 */

function SessionIdentity({ session }: { session: SessionDetailData }) {
  const name = session.streamer.display_name || session.streamer.username;

  return (
    <Card>
      <CardBody className="space-y-5">
        <div className="flex flex-wrap items-start gap-4">
          {session.streamer.profile_image_url && (
            // A plain <img>: the URL is Twitch's own CDN and is never proxied,
            // downloaded or re-hosted by ClipperStash.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={session.streamer.profile_image_url}
              alt=""
              width={48}
              height={48}
              className="size-12 shrink-0 rounded-full border border-border object-cover"
            />
          )}

          <div className="min-w-0 flex-1 space-y-2">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <h2 className="text-lg font-semibold tracking-tight">{name}</h2>
              <SessionStatusBadge status={session.status} />
              {session.category && <Tag>{session.category}</Tag>}
              {session.is_mature && <Tag>Mature</Tag>}
            </div>

            {session.title ? (
              <p className="text-sm text-muted">{session.title}</p>
            ) : (
              <p className="text-sm text-faint">No stream title was recorded.</p>
            )}

            <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
              <Link
                href={`/dashboard/streamers/${session.streamer.id}`}
                className="text-muted underline decoration-border-strong underline-offset-4 transition-colors hover:text-foreground"
              >
                All sessions for this streamer
              </Link>
              <a
                href={session.streamer.channel_url}
                target="_blank"
                rel="noreferrer noopener"
                className="text-muted underline decoration-border-strong underline-offset-4 transition-colors hover:text-foreground"
              >
                View channel on Twitch
              </a>
            </div>
          </div>
        </div>

        <FactGrid columns={4}>
          <Fact label="Started">
            <time dateTime={session.started_at}>{formatTimestamp(session.started_at)}</time>
          </Fact>
          <Fact label={session.status === "live" ? "Live for" : "Lasted"}>
            <span className="tabular-nums">
              {formatElapsed(session.started_at, session.ended_at)}
            </span>
          </Fact>
          <Fact label="Viewers at last check">
            <span className="tabular-nums">{formatCount(session.viewer_count)}</span>
          </Fact>
          <Fact label="Last observed">
            <time dateTime={session.last_observed_at}>
              {formatTimestamp(session.last_observed_at)}
            </time>
          </Fact>
          <Fact label="Ended">
            {session.ended_at ? (
              <time dateTime={session.ended_at}>{formatTimestamp(session.ended_at)}</time>
            ) : (
              <span className="text-muted">Not yet observed as ended</span>
            )}
          </Fact>
          <Fact label="Language">{session.language || <Absent />}</Fact>
          <Fact label="Twitch stream id">
            <code className="font-mono text-xs text-muted">{session.platform_stream_id}</code>
          </Fact>
        </FactGrid>
      </CardBody>
    </Card>
  );
}

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

  const streamerName =
    session.state.kind === "ready"
      ? session.state.data.streamer.display_name || session.state.data.streamer.username
      : null;

  return (
    <div className="space-y-8">
      <Breadcrumbs
        trail={[
          { label: "Dashboard", href: "/dashboard" },
          ...(streamerName && session.state.kind === "ready"
            ? [
                {
                  label: streamerName,
                  href: `/dashboard/streamers/${session.state.data.streamer.id}`,
                },
              ]
            : []),
          { label: "Session" },
        ]}
      />

      <PageHeader
        eyebrow="Stream session"
        title={streamerName ?? "Session"}
        description="One broadcast, and every moment the detector recorded during it."
        actions={
          <Button
            onClick={refreshAll}
            disabled={session.reloading || moments.reloading}
          >
            {session.reloading || moments.reloading ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      {session.state.kind === "loading" && <Skeleton rows={2} label="Loading session" height="h-32" />}

      {session.state.kind === "failed" && (
        <ErrorState
          title="This session could not be loaded"
          message={session.state.message}
          onRetry={session.reload}
        />
      )}

      {session.state.kind === "ready" && (
        <div className="animate-enter space-y-8">
          <SessionIdentity session={session.state.data} />

          <section aria-labelledby="session-counts-heading" className="space-y-4">
            <h2 id="session-counts-heading" className="sr-only">
              Session totals
            </h2>
            <StatGrid columns={4}>
              <Stat
                label="Chat messages"
                value={formatCount(session.state.data.counts.chat_messages)}
                hint="Counted, never quoted."
              />
              <Stat
                label="Moments"
                value={formatCount(session.state.data.counts.total)}
                hint="Windows the detector judged clip-worthy."
              />
              <Stat
                label="Clips ready"
                value={formatCount(session.state.data.counts.created)}
                tone={session.state.data.counts.created > 0 ? "live" : "neutral"}
                hint="Confirmed to exist on Twitch."
              />
              <Stat
                label="Outcome unknown"
                value={formatCount(session.state.data.counts.request_unknown)}
                tone={session.state.data.counts.request_unknown > 0 ? "unknown" : "neutral"}
                hint="Requested, and the result was never learned."
              />
            </StatGrid>
          </section>

          <section aria-labelledby="session-moments-heading">
            <Card>
              <CardHeader
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
                      onChange={(event) =>
                        setFilter(event.target.value as ClipState | "all")
                      }
                      className="h-9 rounded-lg border border-border bg-raised px-2.5 text-sm text-foreground transition-colors hover:border-border-strong"
                    >
                      {FILTERS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                }
              />
              <CardBody>
                {moments.state.kind === "loading" && (
                  <Skeleton rows={4} label="Loading moments" />
                )}

                {moments.state.kind === "failed" && (
                  <ErrorState
                    title="The moments could not be loaded"
                    message={moments.state.message}
                    onRetry={moments.reload}
                  />
                )}

                {moments.state.kind === "ready" &&
                  (moments.state.data.results.length === 0 ? (
                    <EmptyState
                      title={
                        filter === "all"
                          ? "No moments in this session"
                          : `Nothing in "${CLIP_STATE_LABEL[filter as ClipState]}"`
                      }
                      detail={
                        filter === "all"
                          ? "The detector records a candidate only when chat is unusually active for this channel. A quiet session produces none, which is the expected result rather than a fault."
                          : "No moment in this session is in that state. Choose “All moments” to see the rest."
                      }
                      action={
                        filter !== "all" ? (
                          <Button size="sm" onClick={() => setFilter("all")}>
                            Show all moments
                          </Button>
                        ) : undefined
                      }
                    />
                  ) : (
                    <div className="space-y-4">
                      <MomentList
                        moments={moments.state.data.results}
                        threshold={threshold}
                        timeline
                      />
                      <p className="text-sm text-muted">
                        Showing {formatCount(moments.state.data.results.length)} of{" "}
                        <span className="tabular-nums">
                          {formatCount(moments.state.data.count)}
                        </span>
                        {filter !== "all" && (
                          <>
                            {" "}
                            in{" "}
                            <span className={toneText(CLIP_STATE_TONE[filter as ClipState])}>
                              {CLIP_STATE_LABEL[filter as ClipState].toLowerCase()}
                            </span>
                          </>
                        )}
                        .{moments.state.data.has_more && " More remain beyond this page."}
                      </p>
                    </div>
                  ))}
              </CardBody>
            </Card>
          </section>
        </div>
      )}
    </div>
  );
}
