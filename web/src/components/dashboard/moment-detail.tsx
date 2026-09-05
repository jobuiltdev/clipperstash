"use client";

import Link from "next/link";
import { useCallback } from "react";

import { CLIP_STATE_DETAIL, ClipStateBadge } from "@/components/dashboard/clip-state";
import { ScoreBreakdown, ScoreDial } from "@/components/dashboard/score";
import {
  ErrorNotice,
  Fact,
  LoadingLines,
  Panel,
  RefreshButton,
} from "@/components/dashboard/ui";
import { getDetectorConfig, getMoment } from "@/lib/api";
import {
  formatCount,
  formatDuration,
  formatRatio,
  formatTime,
  formatTimestamp,
} from "@/lib/format";
import { useResource } from "@/lib/use-resource";

import type { MomentDetail as MomentDetailData } from "@/lib/api";

/**
 * Everything behind one score.
 *
 * The point of this view is calibration: an operator should be able to look at
 * a moment and say whether the detector was right, which needs the raw
 * aggregates and the windows they were measured over, not just the total.
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
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <p className="text-xs uppercase tracking-wide text-muted">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums">{formatCount(current)}</p>
      <p className="text-xs text-muted">
        {formatCount(baseline)} in the baseline window
      </p>
    </div>
  );
}

function ClipPanel({ moment }: { moment: MomentDetailData }) {
  return (
    <Panel
      headingId="moment-clip-heading"
      title="Clip"
      description={CLIP_STATE_DETAIL[moment.clip_state]}
      actions={<ClipStateBadge state={moment.clip_state} />}
    >
      {moment.clip === null ? (
        <p className="text-sm text-muted">
          No clip request has been recorded for this moment. Clips are requested by hand; nothing
          on this page can request one.
        </p>
      ) : (
        <div className="space-y-4">
          <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Fact label="Requested">
              <time dateTime={moment.clip.requested_at}>
                {formatTimestamp(moment.clip.requested_at)}
              </time>
            </Fact>
            <Fact label="Confirmed">
              {moment.clip.ready_at ? (
                <time dateTime={moment.clip.ready_at}>{formatTimestamp(moment.clip.ready_at)}</time>
              ) : (
                "Not confirmed"
              )}
            </Fact>
            <Fact label="Twitch clip id">
              {moment.clip.twitch_clip_id ? (
                <code className="text-xs">{moment.clip.twitch_clip_id}</code>
              ) : (
                "None recorded"
              )}
            </Fact>
            {moment.clip.title && <Fact label="Title">{moment.clip.title}</Fact>}
            {moment.clip.duration !== null && (
              <Fact label="Duration">{formatDuration(moment.clip.duration)}</Fact>
            )}
            {moment.clip.failure_code && (
              <Fact label="Reason">
                <span className="text-amber-500">{moment.clip.failure_detail}</span>
              </Fact>
            )}
          </dl>

          {moment.clip.twitch_url && (
            <a
              href={moment.clip.twitch_url}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-block rounded-md border border-border px-3 py-1.5 text-sm font-medium"
            >
              Watch on Twitch
            </a>
          )}

          {moment.clip_state === "request_unknown" && (
            <p className="rounded-md border border-amber-500/40 bg-background p-3 text-sm text-muted">
              This request was claimed and its outcome never learned. The clip may exist. Twitch
              accepts no idempotency key on Create Clip, so ClipperStash will not send another
              request automatically — doing so could produce a second clip of an unrelated part of
              the stream.
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}

export function MomentDetail({ momentId }: { momentId: number }) {
  const load = useCallback(() => getMoment(momentId), [momentId]);
  const moment = useResource(load);
  const config = useResource(getDetectorConfig);

  const weights = config.state.kind === "ready" ? config.state.data.weights : undefined;

  return (
    <div className="space-y-6">
      <Panel
        headingId="moment-heading"
        title="Moment"
        actions={<RefreshButton onClick={moment.reload} busy={moment.reloading} />}
      >
        {moment.state.kind === "loading" && <LoadingLines rows={3} label="Loading moment" />}

        {moment.state.kind === "failed" && (
          <ErrorNotice message={moment.state.message} onRetry={moment.reload} />
        )}

        {moment.state.kind === "ready" && (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-3">
              <ScoreDial
                score={moment.state.data.total_score}
                threshold={moment.state.data.threshold}
              />
              <ClipStateBadge state={moment.state.data.clip_state} />
            </div>

            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span>
                {moment.state.data.streamer.display_name || moment.state.data.streamer.username}
              </span>
              <Link
                href={`/dashboard/sessions/${moment.state.data.session_id}`}
                className="underline underline-offset-2"
              >
                Back to the session
              </Link>
            </div>

            <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Fact label="Detected at">
                <time dateTime={moment.state.data.detected_at}>
                  {formatTimestamp(moment.state.data.detected_at)}
                </time>
              </Fact>
              <Fact label="Velocity ratio">
                {formatRatio(moment.state.data.velocity_ratio)} the baseline rate
              </Fact>
              <Fact label="Recorded status">{moment.state.data.status}</Fact>
              <Fact label="Session">{moment.state.data.session_title || "Untitled"}</Fact>
            </dl>
          </div>
        )}
      </Panel>

      {moment.state.kind === "ready" && (
        <>
          <Panel
            headingId="moment-score-heading"
            title="Why this scored what it did"
            description="Each component is 0–100%, combined using the detector's weights."
          >
            <ScoreBreakdown moment={moment.state.data} weights={weights} />
          </Panel>

          <Panel
            headingId="moment-windows-heading"
            title="What the detector compared"
            description="The recent window, measured against the quieter stretch immediately before it. The two never overlap."
          >
            <dl className="grid gap-4 sm:grid-cols-2">
              <Fact label="Current window">
                <time dateTime={moment.state.data.windows.current_start}>
                  {formatTime(moment.state.data.windows.current_start)}
                </time>{" "}
                –{" "}
                <time dateTime={moment.state.data.windows.current_end}>
                  {formatTime(moment.state.data.windows.current_end)}
                </time>
              </Fact>
              <Fact label="Baseline window">
                <time dateTime={moment.state.data.windows.baseline_start}>
                  {formatTime(moment.state.data.windows.baseline_start)}
                </time>{" "}
                –{" "}
                <time dateTime={moment.state.data.windows.baseline_end}>
                  {formatTime(moment.state.data.windows.baseline_end)}
                </time>
              </Fact>
            </dl>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Comparison
                label="Messages"
                current={moment.state.data.current_message_count}
                baseline={moment.state.data.baseline_message_count}
              />
              <Comparison
                label="Distinct chatters"
                current={moment.state.data.current_unique_chatter_count}
                baseline={moment.state.data.baseline_unique_chatter_count}
              />
              <Comparison
                label="Emotes"
                current={moment.state.data.current_emote_count}
                baseline={moment.state.data.baseline_emote_count}
              />
              <Comparison
                label="Reactions"
                current={moment.state.data.current_reaction_count}
                baseline={moment.state.data.baseline_reaction_count}
              />
            </div>

            <p className="text-sm text-muted">
              These are aggregates. ClipperStash stores no chat text or chatter identity against a
              moment, so there is no transcript to show.
            </p>
          </Panel>

          <ClipPanel moment={moment.state.data} />
        </>
      )}
    </div>
  );
}
