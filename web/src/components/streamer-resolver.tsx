"use client";

import { useCallback, useState, type FormEvent } from "react";

import { LiveStatus } from "@/components/live-status";
import { ApiError, resolveStreamer, type Streamer } from "@/lib/api";

type ResolveState =
  | { kind: "idle" }
  | { kind: "resolving" }
  | { kind: "resolved"; streamer: Streamer }
  | { kind: "failed"; message: string };

const FALLBACK_MESSAGE = "Could not resolve that streamer. Try again.";

function messageFor(error: unknown): string {
  // The backend sends a user-safe message with every error it owns; anything
  // else (a network failure, say) gets a generic line rather than raw detail.
  if (error instanceof ApiError && error.message) {
    return error.message;
  }
  return FALLBACK_MESSAGE;
}

function BroadcasterBadge({ type }: { type: string }) {
  if (!type) {
    return null;
  }
  return (
    <span className="rounded-full border border-border px-2 py-0.5 text-xs capitalize text-muted">
      {type}
    </span>
  );
}

export function StreamerResolver() {
  const [value, setValue] = useState("");
  const [state, setState] = useState<ResolveState>({ kind: "idle" });

  const submit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      setState({ kind: "resolving" });
      try {
        const { streamer } = await resolveStreamer(value);
        setState({ kind: "resolved", streamer });
      } catch (error) {
        setState({ kind: "failed", message: messageFor(error) });
      }
    },
    [value],
  );

  const resolving = state.kind === "resolving";

  return (
    <section
      aria-labelledby="streamer-heading"
      className="space-y-4 rounded-lg border border-border bg-surface p-5"
    >
      <h2 id="streamer-heading" className="text-sm font-medium">
        Streamer
      </h2>

      <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row">
        <label htmlFor="streamer-input" className="sr-only">
          Twitch channel URL or username
        </label>
        <input
          id="streamer-input"
          name="streamer"
          type="text"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          disabled={resolving}
          autoComplete="off"
          spellCheck={false}
          placeholder="https://twitch.tv/your-streamer"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={resolving || value.trim().length === 0}
          className="rounded-md border border-border px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"
        >
          {resolving ? "Resolving…" : "Resolve"}
        </button>
      </form>

      {state.kind === "resolving" && (
        <p role="status" className="text-sm text-muted">
          Looking this channel up on Twitch…
        </p>
      )}

      {state.kind === "failed" && (
        <p role="alert" className="text-sm text-rose-500">
          {state.message}
        </p>
      )}

      {state.kind === "resolved" && (
        <div className="space-y-4 rounded-md border border-border bg-background p-4">
          <div className="flex items-start gap-4">
            {state.streamer.profile_image_url && (
              // A plain <img>: the URL is Twitch's own CDN and is never proxied,
              // downloaded or re-hosted by ClipperStash.
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={state.streamer.profile_image_url}
                alt=""
                width={56}
                height={56}
                className="size-14 shrink-0 rounded-full object-cover"
              />
            )}
            <div className="min-w-0 space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-medium">
                  {state.streamer.display_name || state.streamer.username}
                </p>
                <BroadcasterBadge type={state.streamer.broadcaster_type} />
              </div>
              <p className="text-sm text-muted">@{state.streamer.username}</p>
              <a
                href={state.streamer.channel_url}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-block text-sm underline underline-offset-2"
              >
                View on Twitch
              </a>
            </div>
          </div>

          {/* Keyed on the streamer, so resolving a different channel starts
              from an unchecked state rather than showing the previous result. */}
          <LiveStatus key={state.streamer.id} streamerId={state.streamer.id} />
        </div>
      )}

      <p className="text-sm text-muted">
        Paste a Twitch channel URL or username. Live status is checked only when
        you ask; continuous monitoring is not implemented yet.
      </p>
    </section>
  );
}
