"use client";

import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/card";
import { Fact, FactGrid } from "@/components/ui/data";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { getDetectorConfig } from "@/lib/api";
import { formatComponent, formatScore } from "@/lib/format";
import { useResource } from "@/lib/use-resource";

/**
 * The calibration every score on the dashboard was measured against.
 *
 * Fetched rather than restated in the frontend, so this panel cannot drift out
 * of step with the detector. When the values move, this moves with them.
 */
export function DetectorConfigPanel() {
  const { state, reload, reloading } = useResource(getDetectorConfig);

  return (
    <Panel
      headingId="detector-config-heading"
      title="Detector calibration"
      description="Read from the running detector. These are initial values, expected to move once real streams have been replayed."
      actions={
        <Button size="sm" variant="ghost" onClick={reload} disabled={reloading}>
          {reloading ? "Refreshing…" : "Refresh"}
        </Button>
      }
    >
      {state.kind === "loading" && (
        <Skeleton rows={2} label="Loading detector calibration" height="h-14" />
      )}

      {state.kind === "failed" && (
        <ErrorState
          title="The calibration could not be read"
          message={state.message}
          onRetry={reload}
        />
      )}

      {state.kind === "ready" && (
        <div className="space-y-6">
          <FactGrid columns={4}>
            <Fact label="Candidate threshold">
              <span className="tabular-nums">
                {formatScore(state.data.candidate_threshold)}
              </span>
            </Fact>
            <Fact label="Current window">
              <span className="tabular-nums">{state.data.current_window_seconds}s</span>
            </Fact>
            <Fact label="Baseline window">
              <span className="tabular-nums">{state.data.baseline_window_seconds}s</span>
            </Fact>
            <Fact label="Cooldown">
              <span className="tabular-nums">{state.data.cooldown_seconds}s</span>
            </Fact>
            <Fact label="Minimum messages">
              <span className="tabular-nums">{state.data.minimum_current_messages}</span>
            </Fact>
            <Fact label="Minimum chatters">
              <span className="tabular-nums">{state.data.minimum_current_chatters}</span>
            </Fact>
            <Fact label="Clip freshness budget">
              <span className="tabular-nums">{state.data.clip_freshness_seconds}s</span>
            </Fact>
            <Fact label="Verification timeout">
              <span className="tabular-nums">
                {state.data.clip_verification_timeout_seconds}s
              </span>
            </Fact>
          </FactGrid>

          <div className="space-y-3 border-t border-border pt-5">
            <p className="text-[0.6875rem] font-medium uppercase tracking-wider text-faint">
              Score weights
            </p>
            {/* Bars rather than a list: the relative size of each weight is the
                thing worth seeing, and it is hard to read from five numbers. */}
            <ul className="space-y-2.5">
              {Object.entries(state.data.weights).map(([name, weight]) => (
                <li key={name} className="flex items-center gap-3">
                  <span className="w-32 shrink-0 text-sm capitalize">
                    {name.replace(/_/g, " ")}
                  </span>
                  <span
                    aria-hidden
                    className="h-1.5 flex-1 overflow-hidden rounded-full bg-raised"
                  >
                    <span
                      className="block h-full rounded-full bg-foreground/60"
                      style={{ width: `${Math.min(1, weight) * 100}%` }}
                    />
                  </span>
                  <span className="w-12 shrink-0 text-right text-sm tabular-nums text-muted">
                    {formatComponent(weight)}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <p className="text-sm leading-relaxed text-muted">
            The auto-clip threshold of{" "}
            <span className="tabular-nums">
              {formatScore(state.data.auto_clip_threshold)}
            </span>{" "}
            is recorded for reference only. Nothing acts on it: clips are requested by hand.
          </p>
        </div>
      )}
    </Panel>
  );
}
