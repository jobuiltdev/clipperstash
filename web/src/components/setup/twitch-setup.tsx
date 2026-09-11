"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import { buttonStyles } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { Badge, StatusDot, type Tone } from "@/components/ui/status";
import { Skeleton } from "@/components/ui/states";
import {
  TWITCH_OAUTH_START_URL,
  getTwitchConnection,
  type TwitchConnectionResponse,
} from "@/lib/api";

/**
 * The Twitch half of setup.
 *
 * Three different things can be wrong here and they need different answers, so
 * the card says which one it is:
 *
 * 1. the ClipperStash server has no Twitch application configured — an
 *    environment problem, nothing to click;
 * 2. an application is configured but no account has authorized it — connect;
 * 3. an account is connected but the grant is stale or too narrow — reconnect.
 *
 * The backend's connection endpoint reports (2) and (3). It cannot report (1),
 * because a missing client id looks the same as an unconnected account from
 * there. So configuration trouble is surfaced when the API actually tells us
 * about it — any request that fails with `twitch_not_configured` — and the page
 * passes that down rather than the frontend guessing.
 */

type ConnectionState =
  | { kind: "loading" }
  | { kind: "loaded"; connection: TwitchConnectionResponse }
  | { kind: "error"; detail: string };

/**
 * The outcome flag the backend appends when it returns the browser here after an
 * OAuth round trip. Only a short status word ever travels in the URL — never a
 * token or an authorization code.
 *
 * It is read from the address bar exactly once per page load and then cached, so
 * that stripping it from the URL below does not make the message disappear.
 */
let cachedOutcome: string | null | undefined;

function readOutcome(): string | null {
  if (cachedOutcome === undefined) {
    cachedOutcome = new URLSearchParams(window.location.search).get("twitch");
  }
  return cachedOutcome;
}

const noServerOutcome = () => null;
const neverChanges = () => () => {};

function useOAuthOutcome(): string | null {
  const outcome = useSyncExternalStore(neverChanges, readOutcome, noServerOutcome);

  // Tidy the address bar so a refresh does not replay a stale outcome.
  useEffect(() => {
    if (!outcome) {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    params.delete("twitch");
    params.delete("reason");
    const query = params.toString();
    window.history.replaceState(
      null,
      "",
      window.location.pathname + (query ? `?${query}` : ""),
    );
  }, [outcome]);

  return outcome;
}

function describeOutcome(outcome: string): { tone: Tone; message: string } | null {
  switch (outcome) {
    case "connected":
      return { tone: "live", message: "Twitch account connected." };
    case "denied":
      return { tone: "unknown", message: "Authorization was declined on Twitch." };
    case "error":
      return {
        tone: "failed",
        message: "Authorization did not complete. Try connecting again.",
      };
    default:
      return null;
  }
}

function ScopeList({ scopes }: { scopes: string[] }) {
  return (
    <ul className="flex flex-wrap gap-1.5">
      {scopes.map((scope) => (
        <li
          key={scope}
          className="rounded-md border border-border bg-raised px-2 py-0.5 font-mono text-xs text-muted"
        >
          {scope}
        </li>
      ))}
    </ul>
  );
}

/** The environment problem: no Twitch application is configured server-side. */
function NotConfigured() {
  return (
    <div className="space-y-3">
      <p className="text-sm leading-relaxed text-muted">
        The ClipperStash server has no Twitch application configured, so it cannot reach Twitch
        at all. This is set on the backend, not here — there is nothing to connect until it is.
      </p>
      <ul className="space-y-1.5">
        {["TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "TWITCH_REDIRECT_URI"].map((name) => (
          <li key={name} className="flex items-center gap-2 text-sm">
            <StatusDot tone="unknown" />
            <code className="font-mono text-xs text-muted">{name}</code>
          </li>
        ))}
      </ul>
      <p className="text-sm leading-relaxed text-muted">
        Set these in the backend environment, restart the server, then check again.
      </p>
    </div>
  );
}

export function TwitchSetup({ configured }: { configured: boolean }) {
  const [state, setState] = useState<ConnectionState>({ kind: "loading" });
  const outcome = useOAuthOutcome();

  useEffect(() => {
    let cancelled = false;

    getTwitchConnection()
      .then((connection) => {
        if (!cancelled) {
          setState({ kind: "loaded", connection });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({
            kind: "error",
            detail: error instanceof Error ? error.message : "unknown error",
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [outcome]);

  const loaded = state.kind === "loaded" ? state.connection : null;
  const connected = loaded?.connected ?? false;
  const stale = loaded?.requires_reauthorization ?? false;
  const message = outcome ? describeOutcome(outcome) : null;

  const badge = !configured
    ? { tone: "unknown" as Tone, label: "Not configured" }
    : state.kind === "loading"
      ? { tone: "neutral" as Tone, label: "Checking" }
      : state.kind === "error"
        ? { tone: "neutral" as Tone, label: "Status unavailable" }
        : connected && !stale
          ? { tone: "live" as Tone, label: "Connected" }
          : connected
            ? { tone: "unknown" as Tone, label: "Needs reconnecting" }
            : { tone: "neutral" as Tone, label: "Not connected" };

  return (
    <section aria-labelledby="twitch-heading">
      <Card>
        <CardHeader
          headingId="twitch-heading"
          title="Twitch"
          description="ClipperStash reads chat and creates clips as the account connected here."
          actions={
            <Badge tone={badge.tone} dot>
              {badge.label}
            </Badge>
          }
        />

        <CardBody className="space-y-4">
          {message && (
            <p
              role="status"
              className={`rounded-lg border px-3.5 py-2.5 text-sm ${
                message.tone === "live"
                  ? "border-live/30 bg-live/5 text-live"
                  : message.tone === "failed"
                    ? "border-failed/30 bg-failed/5 text-failed"
                    : "border-unknown/30 bg-unknown/5 text-unknown"
              }`}
            >
              {message.message}
            </p>
          )}

          {!configured ? (
            <NotConfigured />
          ) : (
            <>
              {state.kind === "loading" && (
                <Skeleton rows={1} label="Checking Twitch connection" height="h-12" />
              )}

              {state.kind === "error" && (
                <p role="status" className="text-sm leading-relaxed text-muted">
                  The connection status could not be read — {state.detail}
                </p>
              )}

              {loaded && (
                <div className="space-y-4">
                  <p className="text-sm leading-relaxed text-muted">
                    {connected && loaded.account ? (
                      <>
                        Connected as{" "}
                        <span className="font-medium text-foreground">
                          {loaded.account.display_name}
                        </span>{" "}
                        <span className="text-faint">(@{loaded.account.login})</span>.
                      </>
                    ) : (
                      "No Twitch account is connected. Chat ingestion and clip creation both need one."
                    )}
                  </p>

                  {stale && (
                    <p className="rounded-lg border border-unknown/30 bg-unknown/5 px-3.5 py-2.5 text-sm leading-relaxed text-unknown">
                      {connected && !loaded.capabilities.chat_read
                        ? "This connection cannot read chat. Connect again to grant chat access."
                        : "Twitch revoked this authorization. Connect again to restore access."}
                    </p>
                  )}

                  {connected && loaded.capabilities.chat_read && !stale && (
                    <p className="flex items-center gap-2 text-sm text-muted">
                      <StatusDot tone="live" />
                      Chat monitoring is authorized.
                    </p>
                  )}

                  {loaded.scopes.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-[0.6875rem] font-medium uppercase tracking-wider text-faint">
                        Granted scopes
                      </p>
                      <ScopeList scopes={loaded.scopes} />
                    </div>
                  )}

                  {/* A plain link, so the browser navigates to the backend and the
                      backend redirects on to Twitch. The client secret stays
                      server-side and no credential reaches this page. */}
                  <a
                    href={TWITCH_OAUTH_START_URL}
                    className={buttonStyles({
                      variant: connected && !stale ? "secondary" : "primary",
                    })}
                  >
                    {connected ? "Reconnect Twitch" : "Connect Twitch"}
                  </a>
                </div>
              )}
            </>
          )}
        </CardBody>

        <CardFooter>
          Authorization happens between the backend and Twitch. No token ever reaches this
          page.
        </CardFooter>
      </Card>
    </section>
  );
}
