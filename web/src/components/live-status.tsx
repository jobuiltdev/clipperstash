"use client";

import { useCallback, useState } from "react";

import { ApiError, observeStreamer, type ObservedStream } from "@/lib/api";

/**
 * "unavailable" is a first-class state, kept distinct from "offline". A check
 * that failed tells us nothing about the broadcast, and must never be shown as
 * though the channel were known to be idle.
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

function formatStartedAt(value: string): string {
  const started = new Date(value);
  return Number.isNaN(started.getTime()) ? value : started.toLocaleString();
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="shrink-0 text-muted">{label}</span>
      <span className="min-w-0 break-words">{value}</span>
    </div>
  );
}

export function LiveStatus({ streamerId }: { streamerId: number }) {
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
      setState({ kind: "unavailable", message: messageFor(error) });
    }
  }, [streamerId]);

  return (
    <div className="space-y-3 border-t border-border pt-4">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void check()}
          disabled={state.kind === "checking"}
          className="rounded-md border border-border px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"
        >
          {state.kind === "checking" ? "Checking…" : "Check live status"}
        </button>

        {state.kind === "live" && (
          <span className="flex items-center gap-2 text-sm font-medium">
            <span aria-hidden className="size-2.5 rounded-full bg-emerald-500" />
            Live
          </span>
        )}

        {state.kind === "offline" && (
          <span className="flex items-center gap-2 text-sm text-muted">
            <span aria-hidden className="size-2.5 rounded-full bg-neutral-400" />
            Offline
          </span>
        )}
      </div>

      {state.kind === "checking" && (
        <p role="status" className="text-sm text-muted">
          Asking Twitch…
        </p>
      )}

      {state.kind === "live" && (
        <div role="status" className="space-y-1 rounded-md border border-border p-3">
          {state.stream.title && <Row label="Title" value={state.stream.title} />}
          {state.stream.category_name && (
            <Row label="Category" value={state.stream.category_name} />
          )}
          <Row label="Viewers" value={state.stream.viewer_count.toLocaleString()} />
          <Row label="Started" value={formatStartedAt(state.stream.started_at)} />
        </div>
      )}

      {state.kind === "unavailable" && (
        <p role="alert" className="text-sm text-amber-600 dark:text-amber-500">
          Status unknown — {state.message}
        </p>
      )}
    </div>
  );
}
