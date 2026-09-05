/**
 * Minimal typed client for the ClipperStash backend.
 *
 * The backend base URL is supplied by NEXT_PUBLIC_API_BASE_URL so that local
 * development, preview and future deployment targets can each point at their
 * own Django instance without a code change.
 */

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/+$/, "");

export type HealthResponse = {
  status: string;
};

export class ApiError extends Error {
  readonly status: number;
  /** Stable machine code from the backend, when it sent one. */
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** The error envelope the backend uses: `{ error: { code, message } }`. */
type ErrorEnvelope = {
  error?: { code?: unknown; message?: unknown };
};

function readErrorEnvelope(body: unknown): { code: string | null; message: string | null } {
  const envelope = body as ErrorEnvelope | null;
  const error = envelope?.error;
  return {
    code: typeof error?.code === "string" ? error.code : null,
    message: typeof error?.message === "string" ? error.message : null,
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
    cache: "no-store",
  });

  if (!response.ok) {
    let code: string | null = null;
    let message: string | null = null;
    try {
      ({ code, message } = readErrorEnvelope(await response.json()));
    } catch {
      // A non-JSON error body carries nothing useful; fall back to the status.
    }
    throw new ApiError(
      message ?? `Request to ${path} failed`,
      response.status,
      code,
    );
  }

  return (await response.json()) as T;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health/");
}

export type TwitchAccount = {
  id: string;
  login: string;
  display_name: string;
};

/**
 * The safe view of the Twitch connection. The backend deliberately never sends
 * token material, so there is nothing token-shaped to model here.
 */
export type TwitchConnectionResponse = {
  connected: boolean;
  account: TwitchAccount | null;
  scopes: string[];
  requires_reauthorization: boolean;
  /** What the connection is currently authorized to do. Carries no tokens. */
  capabilities: { chat_read: boolean };
};

export function getTwitchConnection(): Promise<TwitchConnectionResponse> {
  return request<TwitchConnectionResponse>("/api/twitch/connection/");
}

/**
 * Entry point for the OAuth flow. The browser navigates here; the backend then
 * redirects on to Twitch, so no credential ever reaches the frontend.
 */
export const TWITCH_OAUTH_START_URL = `${API_BASE_URL}/api/twitch/oauth/start/`;


export type Streamer = {
  id: number;
  platform: string;
  platform_user_id: string;
  username: string;
  display_name: string;
  channel_url: string;
  profile_image_url: string;
  broadcaster_type: string;
  description: string;
};

export type ResolveStreamerResponse = {
  streamer: Streamer;
};

/**
 * Resolve a Twitch channel URL or login.
 *
 * The value is sent to the backend, which parses it and asks Twitch. The
 * frontend never contacts Twitch, and never fetches the submitted URL.
 */
export function resolveStreamer(input: string): Promise<ResolveStreamerResponse> {
  return request<ResolveStreamerResponse>("/api/streamers/resolve/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input }),
  });
}


export type ObservedStream = {
  session_id: number;
  platform_stream_id: string;
  started_at: string;
  title: string;
  category_id: string;
  category_name: string;
  language: string;
  viewer_count: number;
  is_mature: boolean;
};

/**
 * The result of one live check.
 *
 * There is deliberately no "unknown" status: when the backend cannot determine
 * the state it returns an error instead, so a failed check can never be
 * rendered as "offline".
 */
export type ObservationResponse = {
  status: "live" | "offline";
  streamer: { id: number; username: string; display_name: string };
  stream: ObservedStream | null;
};

/** Ask the backend to check a resolved streamer's live state, once. */
export function observeStreamer(streamerId: number): Promise<ObservationResponse> {
  return request<ObservationResponse>(`/api/streamers/${streamerId}/observe/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
}


/* ---------------------------------------------------------------------------
 * Dashboard reads
 *
 * Everything below is read-only. There is no dashboard mutation in this
 * milestone, so none of these helpers takes a method other than the implicit
 * GET, and none of them sends a body.
 * ------------------------------------------------------------------------ */

/**
 * How far a moment got towards becoming a clip.
 *
 * `request_unknown` is not a failure. It means a clip request was claimed and
 * nobody ever learned what Twitch did with it — Twitch's Create Clip takes no
 * idempotency key, so ClipperStash refuses to guess by sending another.
 */
export type ClipState =
  | "not_requested"
  | "requested"
  | "request_unknown"
  | "created"
  | "failed";

export type StreamerRef = {
  id: number;
  username: string;
  display_name: string;
  channel_url: string;
  profile_image_url: string;
};

/** A clip exactly as stored. Twitch's `edit_url` is never persisted or sent. */
export type Clip = {
  id: number;
  twitch_clip_id: string | null;
  twitch_url: string;
  title: string;
  duration: number | null;
  thumbnail_url: string;
  twitch_created_at: string | null;
  requested_at: string;
  ready_at: string | null;
  failure_code: string;
  failure_detail: string;
};

/** One moment row, with the full working behind its score. */
export type MomentSummary = {
  id: number;
  detected_at: string;
  status: string;
  total_score: number;
  velocity_score: number;
  reaction_score: number;
  emote_score: number;
  diversity_score: number;
  absolute_activity_score: number;
  velocity_ratio: number;
  current_message_count: number;
  baseline_message_count: number;
  current_unique_chatter_count: number;
  baseline_unique_chatter_count: number;
  current_emote_count: number;
  baseline_emote_count: number;
  current_reaction_count: number;
  baseline_reaction_count: number;
  clip_state: ClipState;
  clip_id: number | null;
  twitch_clip_id: string | null;
  twitch_url: string;
  failure_code: string;
  requested_at: string | null;
  ready_at: string | null;
};

/** A moment shown away from its own session, so it names its context. */
export type MomentFeedItem = MomentSummary & {
  session_id: number;
  session_title: string;
  streamer: StreamerRef;
};

export type MomentWindows = {
  baseline_start: string;
  baseline_end: string;
  current_start: string;
  current_end: string;
};

export type MomentDetail = MomentFeedItem & {
  windows: MomentWindows;
  /** The detector's threshold at read time, so a score can be read against it. */
  threshold: number;
  failure_detail: string;
  clip: Clip | null;
};

export type SessionSummary = {
  id: number;
  streamer: StreamerRef;
  platform_stream_id: string;
  status: "live" | "ended";
  started_at: string;
  ended_at: string | null;
  title: string;
  category: string;
  language: string;
  viewer_count: number;
  last_observed_at: string;
  moment_count: number;
  clip_created_count: number;
};

/** Moments partitioned by clip state. The parts always sum to `total`. */
export type MomentCounts = {
  total: number;
  not_requested: number;
  requested: number;
  request_unknown: number;
  created: number;
  failed: number;
};

export type SessionDetail = SessionSummary & {
  is_mature: boolean;
  counts: MomentCounts & { chat_messages: number };
};

/** One bounded window over a list, with the total it was drawn from. */
export type Page<T> = {
  count: number;
  limit: number;
  offset: number;
  has_more: boolean;
  results: T[];
};

export type DashboardOverview = {
  generated_at: string;
  counts: {
    streamers: { total: number; active: number };
    sessions: { total: number; live: number; ended: number };
    moments: MomentCounts;
  };
  live_sessions: SessionSummary[];
  recent_sessions: SessionSummary[];
  recent_moments: MomentFeedItem[];
  recent_clips: MomentFeedItem[];
};

/** The detector calibration in force, served from the detector's own config. */
export type DetectorConfig = {
  candidate_threshold: number;
  auto_clip_threshold: number;
  current_window_seconds: number;
  baseline_window_seconds: number;
  cooldown_seconds: number;
  minimum_current_messages: number;
  minimum_current_chatters: number;
  weights: Record<string, number>;
  clip_freshness_seconds: number;
  clip_verification_timeout_seconds: number;
};

export function getDashboardOverview(): Promise<DashboardOverview> {
  return request<DashboardOverview>("/api/dashboard/overview/");
}

/*
 * The backend wraps each read in a named key — `{ session: ... }`, `{ moments:
 * ... }` — so a response is self-describing and can grow a sibling field
 * without breaking anything. The envelope is modelled here and unwrapped at
 * this boundary, so callers work with the resource itself.
 */

export async function getDetectorConfig(): Promise<DetectorConfig> {
  const { detector } = await request<{ detector: DetectorConfig }>(
    "/api/dashboard/detector-config/",
  );
  return detector;
}

export async function getStreamerSessions(streamerId: number): Promise<Page<SessionSummary>> {
  const { sessions } = await request<{ sessions: Page<SessionSummary> }>(
    `/api/streamers/${streamerId}/sessions/`,
  );
  return sessions;
}

export async function getSession(sessionId: number): Promise<SessionDetail> {
  const { session } = await request<{ session: SessionDetail }>(`/api/sessions/${sessionId}/`);
  return session;
}

export async function getSessionMoments(
  sessionId: number,
  options: { clipState?: ClipState } = {},
): Promise<Page<MomentSummary>> {
  const query = options.clipState ? `?clip_state=${options.clipState}` : "";
  const { moments } = await request<{ moments: Page<MomentSummary> }>(
    `/api/sessions/${sessionId}/moments/${query}`,
  );
  return moments;
}

export async function getMoment(momentId: number): Promise<MomentDetail> {
  const { moment } = await request<{ moment: MomentDetail }>(`/api/moments/${momentId}/`);
  return moment;
}
