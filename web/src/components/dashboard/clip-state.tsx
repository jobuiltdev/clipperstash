import type { ClipState } from "@/lib/api";

/**
 * How a clip outcome is named on screen.
 *
 * The wording matters more than it looks. `request_unknown` must never read as
 * a failure: the pipeline claimed a request and then never learned what Twitch
 * did with it, so the clip may well exist. Calling that "Failed" would tell an
 * operator something nobody actually knows.
 */
export const CLIP_STATE_LABEL: Record<ClipState, string> = {
  not_requested: "No clip",
  requested: "Clip requested",
  request_unknown: "Outcome unknown",
  created: "Clip ready",
  failed: "Clip failed",
};

export const CLIP_STATE_DETAIL: Record<ClipState, string> = {
  not_requested: "No clip has been requested for this moment.",
  requested: "Twitch accepted the request. The clip has not been confirmed yet.",
  request_unknown:
    "A clip was requested and the result was never learned. Twitch offers no way to ask whether it acted, so ClipperStash will not send another request automatically.",
  created: "Twitch confirmed the clip exists.",
  failed: "Twitch answered, and the clip was not created.",
};

const CLIP_STATE_CLASS: Record<ClipState, string> = {
  not_requested: "border-border text-muted",
  requested: "border-sky-500/50 text-sky-500",
  request_unknown: "border-amber-500/50 text-amber-500",
  created: "border-emerald-500/50 text-emerald-500",
  failed: "border-rose-500/50 text-rose-500",
};

export function ClipStateBadge({ state }: { state: ClipState }) {
  return (
    <span
      className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-xs ${CLIP_STATE_CLASS[state]}`}
    >
      {CLIP_STATE_LABEL[state]}
    </span>
  );
}
