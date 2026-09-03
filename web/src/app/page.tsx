"use client";

import { useCallback, useEffect, useState } from "react";

import { API_BASE_URL, getHealth } from "@/lib/api";

type BackendState =
  | { kind: "checking" }
  | { kind: "online"; status: string }
  | { kind: "offline"; detail: string };

const STATE_STYLES: Record<BackendState["kind"], string> = {
  checking: "bg-amber-400",
  online: "bg-emerald-500",
  offline: "bg-rose-500",
};

function describe(state: BackendState): string {
  switch (state.kind) {
    case "checking":
      return "Checking backend…";
    case "online":
      return `Backend reachable (status: ${state.status})`;
    case "offline":
      return `Backend unreachable — ${state.detail}`;
  }
}

export default function Home() {
  const [state, setState] = useState<BackendState>({ kind: "checking" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;

    getHealth()
      .then((health) => {
        if (!cancelled) {
          setState({ kind: "online", status: health.status });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({
            kind: "offline",
            detail: error instanceof Error ? error.message : "unknown error",
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const recheck = useCallback(() => {
    setState({ kind: "checking" });
    setAttempt((value) => value + 1);
  }, []);

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-10 px-6 py-20">
      <header className="space-y-3">
        <h1 className="text-4xl font-semibold tracking-tight">ClipperStash</h1>
        <p className="text-lg text-muted">
          Turn live moments into clips automatically.
        </p>
      </header>

      <section
        aria-labelledby="workflow-heading"
        className="space-y-3 rounded-lg border border-border bg-surface p-5"
      >
        <h2 id="workflow-heading" className="text-sm font-medium">
          Streamer
        </h2>
        <div className="flex flex-col gap-3 sm:flex-row">
          <input
            type="url"
            disabled
            aria-describedby="workflow-note"
            placeholder="https://twitch.tv/your-streamer"
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-muted disabled:cursor-not-allowed"
          />
          <button
            type="button"
            disabled
            className="rounded-md border border-border px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"
          >
            Monitor
          </button>
        </div>
        <p id="workflow-note" className="text-sm text-muted">
          Streamer resolution and monitoring are not implemented yet. This input
          is a placeholder for the planned workflow.
        </p>
      </section>

      <section aria-labelledby="status-heading" className="space-y-3">
        <h2 id="status-heading" className="text-sm font-medium">
          Backend status
        </h2>
        <div className="flex items-center gap-3">
          <span
            aria-hidden
            className={`size-2.5 shrink-0 rounded-full ${STATE_STYLES[state.kind]}`}
          />
          <p role="status" className="text-sm">
            {describe(state)}
          </p>
        </div>
        <p className="text-sm text-muted">
          Source: <code>GET {API_BASE_URL}/api/health/</code>
        </p>
        <button
          type="button"
          onClick={recheck}
          disabled={state.kind === "checking"}
          className="rounded-md border border-border px-3 py-1.5 text-sm font-medium disabled:opacity-50"
        >
          Re-check
        </button>
      </section>
    </main>
  );
}
