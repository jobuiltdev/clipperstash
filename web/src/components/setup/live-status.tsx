"use client";

import { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import { Absent, Fact, FactGrid } from "@/components/ui/data";
import { Badge } from "@/components/ui/status";
import { ApiError, observeStreamer, type ObservedStream } from "@/lib/api";
import { formatCount, formatTimestamp } from "@/lib/format";

/**
 * One live check, run when asked.
 *
 * "Unavailable" is a first-class state, kept distinct from "offline". A check
 * that failed tells us nothing about the broadcast, and must never be shown as
 * though the channel were known to be idle — so it gets its own wording and its
 * own colour.
 */
type CheckState =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "live"; stream: ObservedStream }
  | { kind: "offline" }
  | { kind: "unavailable"; message: string };

const FALLBACK_MESSAGE = "The stream status could not be checked right now.";

function messageFor(error: unknown): string {
  if (error instanceof ApiError && error.message) {
    return error.message;
  }
  return FALLBACK_MESSAGE;
}

export function LiveStatus({
  streamerId,
  onUnconfigured,
}: {
  streamerId: number;
  /** Raised when the backend reports it has no Twitch application configured. */
  onUnconfigured?: () => void;
}) {
  const [state, setState] = useState<CheckState>({ kind: "idle" });

  // Checks run only when asked. There is no polling and no timer: continuous
  // monitoring is a later milestone.
  const check = useCallback(async () => {
    setState({ kind: "checking" });
    try {
      const observation = await observeStreamer(streamerId);
      setState(
        observation.status === "live" && observation.stream
          ? { kind: "live", stream: observation.stream }
          : { kind: "offline" },
      );
    } catch (error) {
      if (error instanceof ApiError && error.code === "twitch_not_configured") {
        onUnconfigured?.();
      }
      setState({ kind: "unavailable", message: messageFor(error) });
    }
  }, [streamerId, onUnconfigured]);

  return (
    <div className="space-y-3 border-t border-border pt-4">
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" onClick={() => void check()} disabled={state.kind === "checking"}>
          {state.kind === "checking" ? "Checking…" : "Check live status"}
        </Button>

        {state.kind === "live" && (
          <Badge tone="live" dot pulse>
            Live
          </Badge>
        )}
        {state.kind === "offline" && (
          <Badge tone="neutral" dot>
            Offline
          </Badge>
        )}
        {state.kind === "unavailable" && (
          <Badge tone="unknown" dot>
            Status unknown
          </Badge>
        )}
      </div>

      {state.kind === "checking" && (
        <p role="status" className="text-sm text-muted">
          Asking Twitch…
        </p>
      )}

      {state.kind === "idle" && (
        <p className="text-sm leading-relaxed text-muted">
          Checking opens a stream session when the channel is live, which is what chat
          collection attaches to.
        </p>
      )}

      {state.kind === "live" && (
        <div
          role="status"
          className="rounded-lg border border-border bg-raised px-4 py-3.5"
        >
          <FactGrid columns={2}>
            <Fact label="Title">{state.stream.title || <Absent />}</Fact>
            <Fact label="Category">{state.stream.category_name || <Absent />}</Fact>
            <Fact label="Viewers">
              <span className="tabular-nums">{formatCount(state.stream.viewer_count)}</span>
            </Fact>
            <Fact label="Started">
              <time dateTime={state.stream.started_at}>
                {formatTimestamp(state.stream.started_at)}
              </time>
            </Fact>
          </FactGrid>
        </div>
      )}

      {state.kind === "offline" && (
        <p role="status" className="text-sm leading-relaxed text-muted">
          This channel was not broadcasting at the moment of the check.
        </p>
      )}

      {state.kind === "unavailable" && (
        <p
          role="alert"
          className="rounded-lg border border-unknown/30 bg-unknown/5 px-3.5 py-2.5 text-sm leading-relaxed text-unknown"
        >
          {state.message} Nothing was determined, so this is not the same as offline.
        </p>
      )}
    </div>
  );
}
