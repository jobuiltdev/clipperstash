"use client";

import Link from "next/link";
import { useCallback } from "react";

import { SessionList } from "@/components/dashboard/session-list";
import { Button, buttonStyles } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Stat, StatGrid } from "@/components/ui/data";
import { Breadcrumbs, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { getStreamerSessions } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useResource } from "@/lib/use-resource";

import type { Page, SessionSummary, StreamerRef } from "@/lib/api";

/**
 * Every broadcast ClipperStash has observed for one streamer.
 *
 * The streamer's identity is read from the sessions themselves — the backend
 * has no standalone streamer endpoint, and inventing one for a header would be
 * an API change this page does not need.
 */

function StreamerIdentity({
  streamer,
  page,
}: {
  streamer: StreamerRef;
  page: Page<SessionSummary>;
}) {
  const live = page.results.filter((session) => session.status === "live").length;
  const moments = page.results.reduce((total, session) => total + session.moment_count, 0);
  const clips = page.results.reduce(
    (total, session) => total + session.clip_created_count,
    0,
  );

  return (
    <Card>
      <CardBody className="space-y-5">
        <div className="flex flex-wrap items-center gap-4">
          {streamer.profile_image_url && (
            // A plain <img>: the URL is Twitch's own CDN and is never proxied,
            // downloaded or re-hosted by ClipperStash.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={streamer.profile_image_url}
              alt=""
              width={56}
              height={56}
              className="size-14 shrink-0 rounded-full border border-border object-cover"
            />
          )}
          <div className="min-w-0 space-y-1">
            <h2 className="text-lg font-semibold tracking-tight">
              {streamer.display_name || streamer.username}
            </h2>
            <p className="text-sm text-muted">@{streamer.username}</p>
            <a
              href={streamer.channel_url}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-block text-sm text-muted underline decoration-border-strong underline-offset-4 transition-colors hover:text-foreground"
            >
              View channel on Twitch
            </a>
          </div>
        </div>

        <StatGrid columns={4}>
          <Stat label="Sessions observed" value={formatCount(page.count)} />
          <Stat
            label="Live now"
            value={formatCount(live)}
            tone={live > 0 ? "live" : "neutral"}
            hint="Of the sessions on this page."
          />
          <Stat label="Moments" value={formatCount(moments)} hint="Across this page." />
          <Stat
            label="Clips ready"
            value={formatCount(clips)}
            tone={clips > 0 ? "live" : "neutral"}
            hint="Across this page."
          />
        </StatGrid>
      </CardBody>
    </Card>
  );
}

export function StreamerSessions({ streamerId }: { streamerId: number }) {
  const load = useCallback(() => getStreamerSessions(streamerId), [streamerId]);
  const { state, reload, reloading } = useResource(load);

  const streamer =
    state.kind === "ready" && state.data.results.length > 0
      ? state.data.results[0].streamer
      : null;

  return (
    <div className="space-y-8">
      <Breadcrumbs
        trail={[
          { label: "Dashboard", href: "/dashboard" },
          { label: streamer ? streamer.display_name || streamer.username : "Streamer" },
        ]}
      />

      <PageHeader
        eyebrow="Streamer"
        title={streamer ? streamer.display_name || streamer.username : "Streamer"}
        description="Every broadcast ClipperStash has observed for this channel, most recently started first."
        actions={
          <Button onClick={reload} disabled={reloading}>
            {reloading ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      {state.kind === "loading" && <Skeleton rows={3} label="Loading sessions" height="h-24" />}

      {state.kind === "failed" && (
        <ErrorState
          title="This streamer could not be loaded"
          message={state.message}
          onRetry={reload}
        />
      )}

      {state.kind === "ready" && (
        <div className="animate-enter space-y-6">
          {streamer && <StreamerIdentity streamer={streamer} page={state.data} />}

          <section aria-labelledby="streamer-sessions-heading">
            <Card>
              <CardHeader
                headingId="streamer-sessions-heading"
                title="Sessions"
                description="Most recently started first."
              />
              <CardBody>
                {state.data.results.length === 0 ? (
                  <EmptyState
                    title="No sessions observed"
                    detail="ClipperStash opens a session the first time it observes this streamer live. Check their live state from the setup page to open one."
                    action={
                      <Link href="/" className={buttonStyles({ size: "sm" })}>
                        Go to setup
                      </Link>
                    }
                  />
                ) : (
                  <div className="space-y-4">
                    <SessionList sessions={state.data.results} showStreamer={false} />
                    <p className="text-sm text-muted">
                      Showing{" "}
                      <span className="tabular-nums">
                        {formatCount(state.data.results.length)}
                      </span>{" "}
                      of <span className="tabular-nums">{formatCount(state.data.count)}</span>.
                      {state.data.has_more && " More remain beyond this page."}
                    </p>
                  </div>
                )}
              </CardBody>
            </Card>
          </section>
        </div>
      )}
    </div>
  );
}
