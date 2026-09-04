"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import {
  TWITCH_OAUTH_START_URL,
  getTwitchConnection,
  type TwitchConnectionResponse,
} from "@/lib/api";

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
  const outcome = useSyncExternalStore(
    neverChanges,
    readOutcome,
    noServerOutcome,
  );

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

function describeOutcome(outcome: string): string | null {
  switch (outcome) {
    case "connected":
      return "Twitch account connected.";
    case "denied":
      return "Authorization was declined on Twitch.";
    case "error":
      return "Authorization did not complete. Try connecting again.";
    default:
      return null;
  }
}

export function TwitchConnection() {
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

  const message = outcome ? describeOutcome(outcome) : null;
  const connected = state.kind === "loaded" && state.connection.connected;
  const account = state.kind === "loaded" ? state.connection.account : null;

  return (
    <section aria-labelledby="twitch-heading" className="space-y-3">
      <h2 id="twitch-heading" className="text-sm font-medium">
        Twitch
      </h2>

      {state.kind === "loading" && (
        <p role="status" className="text-sm text-muted">
          Checking Twitch connection…
        </p>
      )}

      {state.kind === "error" && (
        <p role="status" className="text-sm text-muted">
          Connection status unavailable — {state.detail}
        </p>
      )}

      {state.kind === "loaded" && (
        <p role="status" className="text-sm">
          {connected && account
            ? `Connected as ${account.display_name}`
            : "Not connected"}
        </p>
      )}

      {state.kind === "loaded" && state.connection.requires_reauthorization && (
        <p className="text-sm text-muted">
          Twitch revoked this authorization. Connect again to restore access.
        </p>
      )}

      {message && <p className="text-sm text-muted">{message}</p>}

      {/* A plain link, so the browser navigates to the backend and the backend
          redirects on to Twitch. The client secret stays server-side. */}
      <a
        href={TWITCH_OAUTH_START_URL}
        className="inline-block rounded-md border border-border px-3 py-1.5 text-sm font-medium"
      >
        {connected ? "Reconnect Twitch" : "Connect Twitch"}
      </a>

      {connected && state.kind === "loaded" && state.connection.scopes.length > 0 && (
        <p className="text-sm text-muted">
          Granted scopes: {state.connection.scopes.join(", ")}
        </p>
      )}
    </section>
  );
}
