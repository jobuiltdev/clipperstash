"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api";

/**
 * One fetched thing, in exactly one of three states.
 *
 * Modelled as a union rather than as separate `loading`/`error`/`data` flags so
 * that "loaded but also failed" cannot be represented at all.
 */
export type Resource<T> =
  | { kind: "loading" }
  | { kind: "ready"; data: T }
  | { kind: "failed"; message: string };

const FALLBACK_MESSAGE = "Could not load this from the backend.";

export function describeError(error: unknown): string {
  // The backend sends a user-safe message with every error it owns. Anything
  // else — a network failure, a proxy — gets a generic line rather than raw
  // detail that might carry internals.
  if (error instanceof ApiError && error.message) {
    return error.message;
  }
  return FALLBACK_MESSAGE;
}

/**
 * Load something once, and again when the reader asks.
 *
 * Deliberately has no timer, no interval and no subscription: the dashboard
 * refreshes when a person presses refresh, and at no other time. Anything that
 * updated itself would also be quietly re-querying the backend forever.
 *
 * `load` must be stable — wrap it in `useCallback` — or every render would
 * start another request.
 */
export function useResource<T>(load: () => Promise<T>): {
  state: Resource<T>;
  reload: () => void;
  reloading: boolean;
} {
  const [state, setState] = useState<Resource<T>>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;

    load()
      .then((data) => {
        if (!cancelled) {
          setState({ kind: "ready", data });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({ kind: "failed", message: describeError(error) });
        }
      });

    return () => {
      // A reader who navigates away mid-request must not have the answer
      // arrive into an unmounted page.
      cancelled = true;
    };
  }, [load, attempt]);

  const reload = useCallback(() => {
    setState({ kind: "loading" });
    setAttempt((value) => value + 1);
  }, []);

  return { state, reload, reloading: state.kind === "loading" };
}
