"use client";

import Link from "next/link";

import { CLIP_STATE_DETAIL } from "@/components/dashboard/clip-state";
import { MomentList } from "@/components/dashboard/moment-list";
import { SessionList } from "@/components/dashboard/session-list";
import { Button, buttonStyles } from "@/components/ui/button";
import { Panel } from "@/components/ui/card";
import { Stat, StatGrid } from "@/components/ui/data";
import { PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { getDashboardOverview, getDetectorConfig } from "@/lib/api";
import { formatCount, formatTimestamp } from "@/lib/format";
import { useResource } from "@/lib/use-resource";

import type { MomentCounts } from "@/lib/api";

/**
 * The whole installation at a glance.
 *
 * Reads only. One refresh control and no timer: an operator decides when to ask
 * the backend again, so leaving this tab open overnight costs nothing.
 */

function ClipBreakdown({ counts }: { counts: MomentCounts }) {
  return (
    <StatGrid columns={3}>
      <Stat
        label="Clips ready"
        value={formatCount(counts.created)}
        tone={counts.created > 0 ? "live" : "neutral"}
        hint={CLIP_STATE_DETAIL.created}
      />
      <Stat
        label="Clips requested"
        value={formatCount(counts.requested)}
        tone={counts.requested > 0 ? "pending" : "neutral"}
        hint={CLIP_STATE_DETAIL.requested}
      />
      <Stat
        label="Outcome unknown"
        value={formatCount(counts.request_unknown)}
        tone={counts.request_unknown > 0 ? "unknown" : "neutral"}
        hint={CLIP_STATE_DETAIL.request_unknown}
      />
      <Stat
        label="Clips failed"
        value={formatCount(counts.failed)}
        tone={counts.failed > 0 ? "failed" : "neutral"}
        hint={CLIP_STATE_DETAIL.failed}
      />
      <Stat
        label="No clip requested"
        value={formatCount(counts.not_requested)}
        hint={CLIP_STATE_DETAIL.not_requested}
      />
      <Stat
        label="Moments detected"
        value={formatCount(counts.total)}
        hint="Every candidate the detector has recorded. The five states above add up to this."
      />
    </StatGrid>
  );
}

export function Overview() {
  const overview = useResource(getDashboardOverview);
  const config = useResource(getDetectorConfig);

  const threshold =
    config.state.kind === "ready" ? config.state.data.candidate_threshold : null;

  const generatedAt =
    overview.state.kind === "ready" ? overview.state.data.generated_at : null;

  return (
    <div className="space-y-8">
      <PageHeader
        title="Dashboard"
        description="Everything the pipeline has recorded. This page reads; it never runs the detector, contacts Twitch or requests a clip."
        actions={
          <>
            {generatedAt && (
              <span className="text-xs text-faint">
                as of <time dateTime={generatedAt}>{formatTimestamp(generatedAt)}</time>
              </span>
            )}
            <Button variant="secondary" onClick={overview.reload} disabled={overview.reloading}>
              {overview.reloading ? "Refreshing…" : "Refresh"}
            </Button>
          </>
        }
      />

      {overview.state.kind === "failed" && (
        <ErrorState
          title="The backend could not be reached"
          message={overview.state.message}
          onRetry={overview.reload}
        />
      )}

      {overview.state.kind === "loading" && (
        <div className="space-y-8">
          <Skeleton rows={2} label="Loading pipeline counts" height="h-24" />
          <Skeleton rows={3} label="Loading recent activity" />
        </div>
      )}

      {overview.state.kind === "ready" && (
        <div className="animate-enter space-y-8">
          <section aria-labelledby="totals-heading" className="space-y-4">
            <h2 id="totals-heading" className="sr-only">
              Pipeline totals
            </h2>
            <StatGrid columns={3}>
              <Stat
                label="Streamers"
                value={formatCount(overview.state.data.counts.streamers.total)}
                hint={`${formatCount(overview.state.data.counts.streamers.active)} active`}
              />
              <Stat
                label="Sessions"
                value={formatCount(overview.state.data.counts.sessions.total)}
                hint={`${formatCount(overview.state.data.counts.sessions.ended)} ended`}
              />
              <Stat
                label="Live now"
                value={formatCount(overview.state.data.counts.sessions.live)}
                tone={overview.state.data.counts.sessions.live > 0 ? "live" : "neutral"}
                hint="Sessions that were live when last observed."
              />
            </StatGrid>
          </section>

          <Panel
            headingId="clips-heading"
            title="Clip outcomes"
            description="Every detected moment, grouped by what became of its clip."
          >
            <ClipBreakdown counts={overview.state.data.counts.moments} />
          </Panel>

          <div className="grid gap-6 lg:grid-cols-2">
            <Panel
              headingId="live-heading"
              title="Live sessions"
              description="Broadcasts that were live at their last observation."
            >
              {overview.state.data.live_sessions.length === 0 ? (
                <EmptyState
                  title="Nothing live"
                  detail="No observed session is currently live. Observation happens when you ask for it, so this reflects the last check rather than this instant."
                  action={
                    <Link href="/" className={buttonStyles({ size: "sm" })}>
                      Check a streamer
                    </Link>
                  }
                />
              ) : (
                <SessionList sessions={overview.state.data.live_sessions} />
              )}
            </Panel>

            <Panel
              headingId="recent-sessions-heading"
              title="Recent sessions"
              description="The five most recently started broadcasts."
            >
              {overview.state.data.recent_sessions.length === 0 ? (
                <EmptyState
                  title="No sessions observed"
                  detail="Resolve a streamer and check whether they are live to open the first session."
                  action={
                    <Link href="/" className={buttonStyles({ size: "sm" })}>
                      Go to setup
                    </Link>
                  }
                />
              ) : (
                <SessionList sessions={overview.state.data.recent_sessions} />
              )}
            </Panel>
          </div>

          <Panel
            headingId="recent-moments-heading"
            title="Recent moments"
            description="The five most recent candidates, across every streamer."
          >
            {overview.state.data.recent_moments.length === 0 ? (
              <EmptyState
                title="No moments yet"
                detail="The detector records a candidate only when chat is unusually active for that channel. Run it over a session with collected chat to see rows here."
              />
            ) : (
              <MomentList moments={overview.state.data.recent_moments} threshold={threshold} />
            )}
          </Panel>

          <Panel
            headingId="recent-clips-heading"
            title="Recent verified clips"
            description="Clips Twitch is confirmed to have created. A request that was only accepted, one whose outcome was never learned, and one that failed are not clips, so none of them appear here."
          >
            {overview.state.data.recent_clips.length === 0 ? (
              <EmptyState
                title="No verified clips yet"
                detail="No clip has been confirmed to exist on Twitch. Clips are requested by hand from a detected moment; nothing on this dashboard requests one. Requests still awaiting confirmation appear under recent moments."
              />
            ) : (
              <MomentList moments={overview.state.data.recent_clips} threshold={threshold} />
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}
