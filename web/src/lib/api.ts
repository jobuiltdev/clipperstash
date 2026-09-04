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

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new ApiError(`Request to ${path} failed`, response.status);
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
