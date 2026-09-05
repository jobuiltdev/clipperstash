"use client";

import { CLIP_STATE_DETAIL } from "@/components/dashboard/clip-state";
import { MomentList } from "@/components/dashboard/moment-list";
import { SessionList } from "@/components/dashboard/session-list";
import {
  EmptyState,
  ErrorNotice,
  LoadingLines,
  Panel,
  RefreshButton,
  StatCard,
} from "@/components/dashboard/ui";
import { getDashboardOverview, getDetectorConfig } from "@/lib/api";
import { formatCount, formatTimestamp } from "@/lib/format";
import { useResource } from "@/lib/use-resource";

import type { MomentCounts } from "@/lib/api";

/**
 * The whole installation at a glance.
 *
 * Reads only. The page has one refresh control and no timer: an operator
 * decides when to ask the backend again, so leaving this tab open overnight
 * costs nothing.
 */

function MomentCards({ counts }: { counts: MomentCounts }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      <StatCard
        label="Moments detected"
        value={formatCount(counts.total)}
        hint="Every candidate the detector has recorded."
      />
      <StatCard
        label="Clips ready"
        value={formatCount(counts.created)}
        tone="positive"
        hint={CLIP_STATE_DETAIL.created}
      />
      <StatCard
        label="Clips requested"
        value={formatCount(counts.requested)}
        hint={CLIP_STATE_DETAIL.requested}
      />
      <StatCard
        label="Outcome unknown"
        value={formatCount(counts.request_unknown)}
        tone={counts.request_unknown > 0 ? "caution" : "neutral"}
        hint={CLIP_STATE_DETAIL.request_unknown}
      />
      <StatCard
        label="Clips failed"
        value={formatCount(counts.failed)}
        tone={counts.failed > 0 ? "negative" : "neutral"}
        hint={CLIP_STATE_DETAIL.failed}
      />
      <StatCard
        label="No clip requested"
        value={formatCount(counts.not_requested)}
        hint={CLIP_STATE_DETAIL.not_requested}
      />
    </div>
  );
}

export function Overview() {
  const overview = useResource(getDashboardOverview);
  const config = useResource(getDetectorConfig);

  const threshold =
    config.state.kind === "ready" ? config.state.data.candidate_threshold : null;

  return (
    <div className="space-y-6">
      <Panel
        headingId="overview-heading"
        title="Pipeline"
        description={
          overview.state.kind === "ready"
            ? `As of ${formatTimestamp(overview.state.data.generated_at)}. This page does not refresh on its own.`
            : "Counts across every streamer this installation has observed."
        }
        actions={<RefreshButton onClick={overview.reload} busy={overview.reloading} />}
      >
        {overview.state.kind === "loading" && (
          <LoadingLines rows={3} label="Loading pipeline counts" />
        )}

        {overview.state.kind === "failed" && (
          <ErrorNotice message={overview.state.message} onRetry={overview.reload} />
        )}

        {overview.state.kind === "ready" && (
          <div className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-3">
              <StatCard
                label="Streamers"
                value={formatCount(overview.state.data.counts.streamers.total)}
                hint={`${formatCount(overview.state.data.counts.streamers.active)} active`}
              />
              <StatCard
                label="Sessions"
                value={formatCount(overview.state.data.counts.sessions.total)}
                hint={`${formatCount(overview.state.data.counts.sessions.ended)} ended`}
              />
              <StatCard
                label="Live now"
                value={formatCount(overview.state.data.counts.sessions.live)}
                tone={overview.state.data.counts.sessions.live > 0 ? "positive" : "neutral"}
                hint="Sessions ClipperStash last observed as live."
              />
            </div>

            <MomentCards counts={overview.state.data.counts.moments} />
          </div>
        )}
      </Panel>

      <Panel
        headingId="live-heading"
        title="Live sessions"
        description="Broadcasts that were live when they were last observed."
      >
        {overview.state.kind === "loading" && <LoadingLines rows={2} label="Loading live sessions" />}
        {overview.state.kind === "failed" && <ErrorNotice message={overview.state.message} />}
        {overview.state.kind === "ready" &&
          (overview.state.data.live_sessions.length === 0 ? (
            <EmptyState
              title="Nothing live"
              detail="No observed session is currently live. Observation happens when you ask for it, so this reflects the last check rather than this instant."
            />
          ) : (
            <SessionList sessions={overview.state.data.live_sessions} />
          ))}
      </Panel>

      <Panel
        headingId="recent-moments-heading"
        title="Recent moments"
        description="The five most recent candidates, across every streamer."
      >
        {overview.state.kind === "loading" && (
          <LoadingLines rows={3} label="Loading recent moments" />
        )}
        {overview.state.kind === "failed" && <ErrorNotice message={overview.state.message} />}
        {overview.state.kind === "ready" &&
          (overview.state.data.recent_moments.length === 0 ? (
            <EmptyState
              title="No moments yet"
              detail="The detector records a candidate only when chat is unusually active for that channel. Run the detector over a session with chat to see rows here."
            />
          ) : (
            <MomentList moments={overview.state.data.recent_moments} threshold={threshold} />
          ))}
      </Panel>

      <Panel
        headingId="recent-clips-heading"
        title="Recent verified clips"
        description="Clips Twitch is confirmed to have created. A request that was only accepted, one whose outcome was never learned, and one that failed are not clips, so none of them appear here."
      >
        {overview.state.kind === "loading" && <LoadingLines rows={2} label="Loading recent verified clips" />}
        {overview.state.kind === "failed" && <ErrorNotice message={overview.state.message} />}
        {overview.state.kind === "ready" &&
          (overview.state.data.recent_clips.length === 0 ? (
            <EmptyState
              title="No verified clips yet"
              detail="No clip has been confirmed to exist on Twitch. Clips are requested by hand from a detected moment; nothing on this dashboard requests one. Requests still awaiting confirmation appear under recent moments."
            />
          ) : (
            <MomentList moments={overview.state.data.recent_clips} threshold={threshold} />
          ))}
      </Panel>

      <Panel
        headingId="recent-sessions-heading"
        title="Recent sessions"
        description="The five most recently started broadcasts."
      >
        {overview.state.kind === "loading" && (
          <LoadingLines rows={2} label="Loading recent sessions" />
        )}
        {overview.state.kind === "failed" && <ErrorNotice message={overview.state.message} />}
        {overview.state.kind === "ready" &&
          (overview.state.data.recent_sessions.length === 0 ? (
            <EmptyState
              title="No sessions observed"
              detail="Resolve a streamer on the home page and check whether they are live to open the first session."
            />
          ) : (
            <SessionList sessions={overview.state.data.recent_sessions} />
          ))}
      </Panel>
    </div>
  );
}
