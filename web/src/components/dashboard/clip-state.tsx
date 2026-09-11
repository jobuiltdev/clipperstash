import { Badge, type Tone } from "@/components/ui/status";

import type { ClipState } from "@/lib/api";

/**
 * How a clip outcome is named on screen.
 *
 * The wording matters more than it looks. `request_unknown` must never read as
 * a failure: the pipeline claimed a request and then never learned what Twitch
 * did with it, so the clip may well exist. Calling that "Failed" would tell an
 * operator something nobody actually knows — which is why it has its own tone
 * as well as its own words.
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

export const CLIP_STATE_TONE: Record<ClipState, Tone> = {
  not_requested: "neutral",
  requested: "pending",
  request_unknown: "unknown",
  created: "live",
  failed: "failed",
};

export function ClipStateBadge({ state }: { state: ClipState }) {
  return (
    <Badge tone={CLIP_STATE_TONE[state]} dot>
      {CLIP_STATE_LABEL[state]}
    </Badge>
  );
}
