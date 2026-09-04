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
