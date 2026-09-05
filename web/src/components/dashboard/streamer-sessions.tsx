"use client";

import { useCallback } from "react";

import { SessionList } from "@/components/dashboard/session-list";
import {
  EmptyState,
  ErrorNotice,
  LoadingLines,
  Panel,
  RefreshButton,
} from "@/components/dashboard/ui";
import { getStreamerSessions } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useResource } from "@/lib/use-resource";

/** Every broadcast ClipperStash has observed for one streamer. */
export function StreamerSessions({ streamerId }: { streamerId: number }) {
  const load = useCallback(() => getStreamerSessions(streamerId), [streamerId]);
  const { state, reload, reloading } = useResource(load);

  const streamer =
    state.kind === "ready" && state.data.results.length > 0
      ? state.data.results[0].streamer
      : null;

  return (
    <Panel
      headingId="streamer-sessions-heading"
      title={
        streamer ? `Sessions for ${streamer.display_name || streamer.username}` : "Sessions"
      }
      description="Most recently started first."
      actions={<RefreshButton onClick={reload} busy={reloading} />}
    >
      {state.kind === "loading" && <LoadingLines rows={3} label="Loading sessions" />}

      {state.kind === "failed" && <ErrorNotice message={state.message} onRetry={reload} />}

      {state.kind === "ready" &&
        (state.data.results.length === 0 ? (
          <EmptyState
            title="No sessions observed"
            detail="ClipperStash opens a session the first time it observes this streamer live. Check their live state from the home page to open one."
          />
        ) : (
          <>
            <SessionList sessions={state.data.results} />
            <p className="text-sm text-muted">
              Showing {formatCount(state.data.results.length)} of{" "}
              {formatCount(state.data.count)}.
              {state.data.has_more && " More remain beyond this page."}
            </p>
          </>
        ))}
    </Panel>
  );
}
