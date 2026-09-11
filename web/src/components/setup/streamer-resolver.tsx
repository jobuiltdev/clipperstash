"use client";

import Link from "next/link";
import { useCallback, useState, type FormEvent } from "react";

import { LiveStatus } from "@/components/setup/live-status";
import { Button, buttonStyles } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Tag } from "@/components/ui/status";
import { ErrorState } from "@/components/ui/states";
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

export function StreamerResolver({
  onUnconfigured,
}: {
  /** Raised when the backend reports it has no Twitch application configured. */
  onUnconfigured?: () => void;
}) {
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
        if (error instanceof ApiError && error.code === "twitch_not_configured") {
          onUnconfigured?.();
        }
        setState({ kind: "failed", message: messageFor(error) });
      }
    },
    [value, onUnconfigured],
  );

  const resolving = state.kind === "resolving";

  return (
    <section aria-labelledby="streamer-heading">
      <Card>
        <CardHeader
          headingId="streamer-heading"
          title="Streamer"
          description="Paste a Twitch channel URL or username. ClipperStash resolves it against Twitch and remembers the account."
        />

        <CardBody className="space-y-4">
          <form onSubmit={submit} className="flex flex-col gap-2.5 sm:flex-row">
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
              className="h-9 w-full rounded-lg border border-border bg-raised px-3 text-sm text-foreground placeholder:text-faint transition-colors hover:border-border-strong disabled:opacity-60"
            />
            <Button
              type="submit"
              variant="primary"
              disabled={resolving || value.trim().length === 0}
              className="shrink-0"
            >
              {resolving ? "Resolving…" : "Resolve"}
            </Button>
          </form>

          {resolving && (
            <p role="status" className="text-sm text-muted">
              Looking this channel up on Twitch…
            </p>
          )}

          {state.kind === "failed" && (
            <ErrorState title="Could not resolve that channel" message={state.message} />
          )}

          {state.kind === "resolved" && (
            <div className="animate-enter space-y-4 rounded-lg border border-border bg-raised px-4 py-4">
              <div className="flex flex-wrap items-start gap-4">
                {state.streamer.profile_image_url && (
                  // A plain <img>: the URL is Twitch's own CDN and is never
                  // proxied, downloaded or re-hosted by ClipperStash.
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={state.streamer.profile_image_url}
                    alt=""
                    width={48}
                    height={48}
                    className="size-12 shrink-0 rounded-full border border-border object-cover"
                  />
                )}
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">
                      {state.streamer.display_name || state.streamer.username}
                    </p>
                    {state.streamer.broadcaster_type && (
                      <Tag>{state.streamer.broadcaster_type}</Tag>
                    )}
                  </div>
                  <p className="text-sm text-muted">@{state.streamer.username}</p>
                  <div className="flex flex-wrap gap-2 pt-1">
                    <Link
                      href={`/dashboard/streamers/${state.streamer.id}`}
                      className={buttonStyles({ size: "sm" })}
                    >
                      Session history
                    </Link>
                    <a
                      href={state.streamer.channel_url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className={buttonStyles({ size: "sm", variant: "ghost" })}
                    >
                      View on Twitch
                    </a>
                  </div>
                </div>
              </div>

              {/* Keyed on the streamer, so resolving a different channel starts
                  from an unchecked state rather than showing the previous result. */}
              <LiveStatus
                key={state.streamer.id}
                streamerId={state.streamer.id}
                onUnconfigured={onUnconfigured}
              />
            </div>
          )}
        </CardBody>
      </Card>
    </section>
  );
}
