"use client";

import Link from "next/link";
import { useCallback } from "react";

import {
  CLIP_STATE_DETAIL,
  CLIP_STATE_LABEL,
  CLIP_STATE_TONE,
  ClipStateBadge,
} from "@/components/dashboard/clip-state";
import { ScoreBreakdown, ScoreDial } from "@/components/dashboard/score";
import { Button, buttonStyles } from "@/components/ui/button";
import { Card, CardBody, CardHeader, Panel } from "@/components/ui/card";
import { Absent, Fact, FactGrid } from "@/components/ui/data";
import { Breadcrumbs, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { toneText } from "@/components/ui/status";
import {
  formatCount,
  formatDuration,
  formatRatio,
  formatTime,
  formatTimestamp,
} from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import { getDetectorConfig, getMoment } from "@/lib/api";

import type { MomentDetail as MomentDetailData } from "@/lib/api";

/**
 * Everything behind one score.
 *
 * The point of this view is calibration: an operator should be able to look at a
 * moment and say whether the detector was right, which needs the raw aggregates
 * and the windows they were measured over, not just the total.
 */

function Comparison({
  label,
  current,
  baseline,
}: {
  label: string;
  current: number;
  baseline: number;
}) {
  // How much busier the current window was than the stretch before it. Shown as
  // a multiplier because that is the question being asked of every signal.
  const multiple = baseline > 0 ? current / baseline : null;

  return (
    <div className="rounded-lg border border-border bg-raised px-4 py-3">
      <p className="text-[0.6875rem] font-medium uppercase tracking-wider text-faint">
        {label}
      </p>
      <p className="mt-1 text-xl font-semibold tabular-nums">{formatCount(current)}</p>
      <p className="mt-0.5 text-xs text-muted">
        <span className="tabular-nums">{formatCount(baseline)}</span> in baseline
        {multiple !== null && multiple !== 1 && (
          <span className="text-faint"> · {formatRatio(multiple)}</span>
        )}
      </p>
    </div>
  );
}

function ClipPanel({ moment }: { moment: MomentDetailData }) {
  return (
    <section aria-labelledby="moment-clip-heading">
      <Card>
        <CardHeader
          headingId="moment-clip-heading"
          title="Clip"
          description={CLIP_STATE_DETAIL[moment.clip_state]}
          actions={<ClipStateBadge state={moment.clip_state} />}
        />
        <CardBody>
          {moment.clip === null ? (
            <EmptyState
              title="No clip requested"
              detail="Nothing has been recorded against this moment. Clips are requested by hand from the command line; nothing on this page can request one."
            />
          ) : (
            <div className="space-y-5">
              <FactGrid columns={3}>
                <Fact label="Requested">
                  <time dateTime={moment.clip.requested_at}>
                    {formatTimestamp(moment.clip.requested_at)}
                  </time>
                </Fact>
                <Fact label="Confirmed">
                  {moment.clip.ready_at ? (
                    <time dateTime={moment.clip.ready_at}>
                      {formatTimestamp(moment.clip.ready_at)}
                    </time>
                  ) : (
                    <span className="text-muted">Not confirmed</span>
                  )}
                </Fact>
                <Fact label="Twitch clip id">
                  {moment.clip.twitch_clip_id ? (
                    <code className="font-mono text-xs text-muted">
                      {moment.clip.twitch_clip_id}
                    </code>
                  ) : (
                    <span className="text-muted">None recorded</span>
                  )}
                </Fact>
                <Fact label="Title">{moment.clip.title || <Absent />}</Fact>
                <Fact label="Duration">
                  {moment.clip.duration !== null ? (
                    <span className="tabular-nums">{formatDuration(moment.clip.duration)}</span>
                  ) : (
                    <Absent />
                  )}
                </Fact>
                {moment.clip.failure_code && (
                  <Fact label="Reason">
                    <span className={toneText(CLIP_STATE_TONE[moment.clip_state])}>
                      {moment.clip.failure_detail}
                    </span>
                  </Fact>
                )}
              </FactGrid>

              {moment.clip.twitch_url && (
                <a
                  href={moment.clip.twitch_url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className={buttonStyles({ variant: "primary" })}
                >
                  Watch on Twitch
                </a>
              )}

              {moment.clip_state === "request_unknown" && (
                <p className="rounded-lg border border-unknown/30 bg-unknown/5 px-4 py-3 text-sm leading-relaxed text-muted">
                  This request was claimed and its outcome never learned. The clip may exist.
                  Twitch accepts no idempotency key on Create Clip, so ClipperStash will not
                  send another request automatically — doing so could produce a second clip of
                  an unrelated part of the stream.
                </p>
              )}
            </div>
          )}
        </CardBody>
      </Card>
    </section>
  );
}

export function MomentDetail({ momentId }: { momentId: number }) {
  const load = useCallback(() => getMoment(momentId), [momentId]);
  const moment = useResource(load);
  const config = useResource(getDetectorConfig);

  const weights = config.state.kind === "ready" ? config.state.data.weights : undefined;
  const data = moment.state.kind === "ready" ? moment.state.data : null;
  const streamerName = data
    ? data.streamer.display_name || data.streamer.username
    : null;

  return (
    <div className="space-y-8">
      <Breadcrumbs
        trail={[
          { label: "Dashboard", href: "/dashboard" },
          ...(data && streamerName
            ? [
                { label: streamerName, href: `/dashboard/streamers/${data.streamer.id}` },
                { label: "Session", href: `/dashboard/sessions/${data.session_id}` },
              ]
            : []),
          { label: "Moment" },
        ]}
      />

      <PageHeader
        eyebrow="Detected moment"
        title={
          data ? (
            <span className="flex flex-wrap items-center gap-3">
              <time dateTime={data.detected_at}>{formatTimestamp(data.detected_at)}</time>
              <ClipStateBadge state={data.clip_state} />
            </span>
          ) : (
            "Moment"
          )
        }
        description={
          data
            ? `Detected in ${streamerName}'s session. ${CLIP_STATE_DETAIL[data.clip_state]}`
            : "One window of chat the detector judged clip-worthy."
        }
        actions={
          <Button onClick={moment.reload} disabled={moment.reloading}>
            {moment.reloading ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      {moment.state.kind === "loading" && (
        <Skeleton rows={3} label="Loading moment" height="h-28" />
      )}

      {moment.state.kind === "failed" && (
        <ErrorState
          title="This moment could not be loaded"
          message={moment.state.message}
          onRetry={moment.reload}
        />
      )}

      {data && (
        <div className="animate-enter space-y-6">
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
            <Panel
              headingId="moment-score-heading"
              title="Score"
              description="On the published 0–100 scale, against the threshold in force."
            >
              <div className="space-y-6">
                <ScoreDial score={data.total_score} threshold={data.threshold} />

                <FactGrid columns={2}>
                  <Fact label="Velocity ratio">
                    <span className="tabular-nums">{formatRatio(data.velocity_ratio)}</span> the
                    baseline rate
                  </Fact>
                  <Fact label="Recorded status">
                    <span className={toneText(CLIP_STATE_TONE[data.clip_state])}>
                      {CLIP_STATE_LABEL[data.clip_state]}
                    </span>
                  </Fact>
                  <Fact label="Session">
                    <Link
                      href={`/dashboard/sessions/${data.session_id}`}
                      className="underline decoration-border-strong underline-offset-4 transition-colors hover:text-foreground"
                    >
                      {data.session_title || "Untitled session"}
                    </Link>
                  </Fact>
                  <Fact label="Streamer">
                    <Link
                      href={`/dashboard/streamers/${data.streamer.id}`}
                      className="underline decoration-border-strong underline-offset-4 transition-colors hover:text-foreground"
                    >
                      {streamerName}
                    </Link>
                  </Fact>
                </FactGrid>
              </div>
            </Panel>

            <Panel
              headingId="moment-components-heading"
              title="Why it scored that"
              description="Each component runs 0–100%, combined using the detector's weights."
            >
              <ScoreBreakdown moment={data} weights={weights} />
            </Panel>
          </div>

          <Panel
            headingId="moment-windows-heading"
            title="What the detector compared"
            description="The recent window, measured against the quieter stretch immediately before it. The two never overlap."
          >
            <div className="space-y-5">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-lg border border-border bg-raised px-4 py-3">
                  <p className="text-[0.6875rem] font-medium uppercase tracking-wider text-faint">
                    Current window
                  </p>
                  <p className="mt-1 font-mono text-sm tabular-nums">
                    <time dateTime={data.windows.current_start}>
                      {formatTime(data.windows.current_start)}
                    </time>
                    <span className="text-faint"> → </span>
                    <time dateTime={data.windows.current_end}>
                      {formatTime(data.windows.current_end)}
                    </time>
                  </p>
                </div>
                <div className="rounded-lg border border-border bg-raised px-4 py-3">
                  <p className="text-[0.6875rem] font-medium uppercase tracking-wider text-faint">
                    Baseline window
                  </p>
                  <p className="mt-1 font-mono text-sm tabular-nums text-muted">
                    <time dateTime={data.windows.baseline_start}>
                      {formatTime(data.windows.baseline_start)}
                    </time>
                    <span className="text-faint"> → </span>
                    <time dateTime={data.windows.baseline_end}>
                      {formatTime(data.windows.baseline_end)}
                    </time>
                  </p>
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Comparison
                  label="Messages"
                  current={data.current_message_count}
                  baseline={data.baseline_message_count}
                />
                <Comparison
                  label="Distinct chatters"
                  current={data.current_unique_chatter_count}
                  baseline={data.baseline_unique_chatter_count}
                />
                <Comparison
                  label="Emotes"
                  current={data.current_emote_count}
                  baseline={data.baseline_emote_count}
                />
                <Comparison
                  label="Reactions"
                  current={data.current_reaction_count}
                  baseline={data.baseline_reaction_count}
                />
              </div>

              <p className="text-sm leading-relaxed text-muted">
                These are aggregates. ClipperStash stores no chat text or chatter identity
                against a moment, so there is no transcript to show.
              </p>
            </div>
          </Panel>

          <ClipPanel moment={data} />
        </div>
      )}
    </div>
  );
}
