"use client";

import { ErrorNotice, Fact, LoadingLines, Panel, RefreshButton } from "@/components/dashboard/ui";
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
      actions={<RefreshButton onClick={reload} busy={reloading} />}
    >
      {state.kind === "loading" && <LoadingLines rows={2} label="Loading detector calibration" />}

      {state.kind === "failed" && <ErrorNotice message={state.message} onRetry={reload} />}

      {state.kind === "ready" && (
        <>
          <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="Candidate threshold">
              {formatScore(state.data.candidate_threshold)}
            </Fact>
            <Fact label="Current window">{state.data.current_window_seconds}s</Fact>
            <Fact label="Baseline window">{state.data.baseline_window_seconds}s</Fact>
            <Fact label="Cooldown">{state.data.cooldown_seconds}s</Fact>
            <Fact label="Minimum messages">{state.data.minimum_current_messages}</Fact>
            <Fact label="Minimum chatters">{state.data.minimum_current_chatters}</Fact>
            <Fact label="Clip freshness budget">
              {state.data.clip_freshness_seconds}s
            </Fact>
            <Fact label="Clip verification timeout">
              {state.data.clip_verification_timeout_seconds}s
            </Fact>
          </dl>

          <div className="space-y-1 border-t border-border pt-4">
            <p className="text-xs uppercase tracking-wide text-muted">Score weights</p>
            <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
              {Object.entries(state.data.weights).map(([name, weight]) => (
                <li key={name} className="capitalize">
                  {name.replace(/_/g, " ")}{" "}
                  <span className="tabular-nums text-muted">{formatComponent(weight)}</span>
                </li>
              ))}
            </ul>
          </div>

          <p className="text-sm text-muted">
            The auto-clip threshold of {formatScore(state.data.auto_clip_threshold)} is
            recorded for reference only. Nothing acts on it: clips are requested by hand.
          </p>
        </>
      )}
    </Panel>
  );
}
