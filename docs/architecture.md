# ClipperStash Architecture

## 1. Product objective

ClipperStash watches an authorized or target Twitch stream, looks for moments that
are likely worth keeping, and turns those moments into Twitch clips that are
collected in a single dashboard.

The intended end-to-end product shape is:

```
Twitch stream
  -> realtime monitoring
  -> interesting moment detection
  -> Twitch clip creation
  -> clip collection / dashboard
```

A later stage of the product may take clips the operator is authorized to use and
render them as short-form vertical assets for platforms such as TikTok,
Instagram Reels and YouTube Shorts.

## 2. V0 boundaries

V0 is a working vertical slice, not a finished product. It is being built in
milestones, and everything not yet delivered is marked `NOT IMPLEMENTED` below
rather than stubbed in code.

**Milestone 0 — foundation. IMPLEMENTED.**

- A Django + Django REST Framework backend with the product's app boundaries
  created but empty.
- A `GET /api/health/` endpoint returning a fixed `{"status": "ok"}` payload.
- PostgreSQL, Redis and Celery configured entirely from environment variables.
- A Next.js + TypeScript + Tailwind application shell that reports backend
  connectivity.
- Docker Compose definitions for the two backing services used locally.

**Milestone 1 — Twitch API and OAuth foundation. IMPLEMENTED.**

- A single centralized Twitch HTTP client, with typed integration exceptions.
- App access tokens via the Client Credentials grant, cached in Redis.
- User access tokens via the Authorization Code grant, with server-side state.
- Storage of one connected Twitch identity, with reactive token refresh on a
  Twitch 401 and on-demand token validation.
- A safe connection-status endpoint and a minimal frontend control to exercise
  the flow.

This is the plumbing only. It can authorize an account and make an authenticated
Twitch request; it does not yet do anything with either.

Explicitly still out of scope:

- Streamer URL parsing and streamer resolution.
- Live/offline detection and stream sessions.
- Twitch EventSub subscriptions and chat ingestion.
- Moment scoring or detection.
- Clip creation, clip download or any media handling.
- FFmpeg, transcription, captions and vertical rendering.
- ClipperStash end-user authentication, accounts, billing and social publishing.
- Scheduled/background work of any kind, including the startup-and-hourly Twitch
  token validation cadence described below.
- Production deployment infrastructure.

Anything in those categories is documented here as a boundary rather than
implemented.

## 3. Current architecture

### Repository layout

```
backend/    Django project (`clipperstash`) and the product apps
web/        Next.js application shell
docs/       Project documentation
docker-compose.yml   PostgreSQL and Redis for local development
.env.example         Placeholder configuration for local development
```

### Backend

The Django project is `backend/clipperstash/`. It holds settings, URL routing,
the health view and the Celery application. Product code lives under
`backend/apps/`, split into the five domains the pipeline will need:

| App          | Responsibility                                              | Status                  |
| ------------ | ----------------------------------------------------------- | ----------------------- |
| `streamers`  | Streamer identity, resolution from a URL, stored channels    | NOT IMPLEMENTED         |
| `twitch`     | Twitch API client, OAuth and credential handling             | IMPLEMENTED (see below) |
| `monitoring` | Stream sessions and realtime signal ingestion                | NOT IMPLEMENTED         |
| `moments`    | Moment scoring and interesting-moment detection              | NOT IMPLEMENTED         |
| `clips`      | Clip records, clip creation and the dashboard's read models  | NOT IMPLEMENTED         |

Twitch EventSub and chat ingestion will also live in `twitch`, and are NOT
IMPLEMENTED. Every app other than `twitch` still contains only its `AppConfig`
and an empty `models` module.

### Twitch integration

All Twitch HTTP behavior lives in `backend/apps/twitch/`. Domain code in later
milestones calls the service layer and never constructs a Twitch request itself.

| Module          | Responsibility                                                       |
| --------------- | -------------------------------------------------------------------- |
| `client.py`     | The only place Twitch URLs, headers, timeouts and JSON decoding live   |
| `oauth.py`      | Scopes, state creation and consumption, authorization URL construction |
| `services.py`   | App-token cache, connection lifecycle, refresh, validation, retry      |
| `models.py`     | `TwitchConnection` persistence and token accessors                     |
| `exceptions.py` | Typed integration errors                                               |
| `views.py`      | The three HTTP entry points                                            |

The exception hierarchy is `TwitchError` with `TwitchConfigurationError`,
`TwitchAuthenticationError`, `TwitchAPIError`, `TwitchOAuthStateError` and
`TwitchAuthorizationDeniedError` beneath it. The HTTP library's own exceptions
never escape `client.py`.

**App Access Token (Client Credentials).** `services.get_app_access_token()`
requests `grant_type=client_credentials` and caches the result in Redis under
`twitch:app_access_token`. The cache TTL is Twitch's `expires_in` minus a
300-second safety margin, so an almost-expired token is never reused; a token
shorter-lived than the margin is used once and not cached. The token is not
persisted to the database, because it is derivable from the client credentials
at any time. Two concurrent cold requests may each mint a token, which is
harmless, so no distributed lock is used at this stage.

**User Access Token (Authorization Code).** The browser is sent to
`/api/twitch/oauth/start/`, which mints a 32-byte random `state`, records it
server-side in the cache with a 10-minute TTL, and redirects to Twitch with
`response_type=code`. Twitch returns to `/api/twitch/oauth/callback/`, which
consumes the state (a single atomic delete, so a replayed state is rejected),
then exchanges the code for tokens over the backend's own connection to Twitch.
The implicit flow is deliberately not supported: it would hand a token to the
browser and leave the client secret unusable for refresh.

**Requested scope.** `clips:edit` and nothing else. It is what clip creation will
need in a later milestone. In particular `user:read:email` is not requested:
ClipperStash has no use for the account's email address.

**Connected identity.** After a successful exchange the backend calls
`GET /helix/users` with the new user token. No query parameter is needed, as the
token identifies its own user. The Twitch user ID, login and display name are
persisted on `TwitchConnection`. This is a single local connection, not a
ClipperStash account system; the code avoids assumptions that would block
multi-user support later, but no multi-tenancy is built now.

**Token storage.** Access and refresh tokens are backend-only. They are never
returned by an API response, never placed in a URL, never logged, and never
included in a model's `__str__` or `__repr__`. They are currently stored as
plain columns, which is adequate for local V0 development and is **not** a
production posture: protecting them at rest is a deployment and security concern
that has not been designed yet, and no key-management scheme has been invented
for V0. All reads and writes go through accessors on the model
(`get_access_token`, `get_refresh_token`, `apply_tokens`), so stronger
protection can be introduced in one place later.

**Refresh is reactive, not scheduled.** A Helix request is made with the stored
access token as it is. `token_expires_at` is retained as metadata describing what
Twitch reported at grant time, and is deliberately *not* the trigger for an
automatic refresh: Twitch's guidance for Authorization Code user tokens is to
react to a `401 Unauthorized` rather than to pre-empt one from a locally tracked
expiry. A token can be revoked well before that timestamp, and can still be
accepted after it, so only Twitch's answer decides.

On a 401 the service layer performs exactly one refresh via
`grant_type=refresh_token` and replays the original request exactly once. A
rotated refresh token is persisted; when Twitch omits one, the existing token is
kept. Expiry and scopes are updated from the grant. If the replayed request is
also rejected, there is no second refresh: the connection is marked
`requires_reauthorization` and a typed authentication error propagates. A refresh
that itself fails does the same. The cycle is bounded and cannot loop.

`services.refresh_connection()` remains available as a direct entry point for
internal use and tests; it is simply not on the automatic request path.

**Token validation.** `services.validate_connection()` calls Twitch's
`/oauth2/validate` endpoint and raises a typed authentication error, flagging the
connection, when Twitch reports the token invalid or revoked.

Twitch requires third-party applications that maintain OAuth sessions to validate
their access tokens when the application or session starts, and hourly
thereafter. ClipperStash provides the mechanism but does **not** yet run it on a
schedule:

```
startup + hourly token validation   NOT IMPLEMENTED
```

There is no Celery Beat schedule and no recurring job in V0 — a test asserts the
beat schedule is empty. Automating this cadence is a deferred runtime obligation
belonging to a later runtime-monitoring and hardening milestone.

Configuration is read from environment variables via `django-environ`, with a
local `.env` file loaded when present. There is a single settings module; a
larger per-environment hierarchy is not warranted at this size and can be
introduced when a deployment target actually exists.

### API surface

| Method | Path                          | Notes                                              |
| ------ | ----------------------------- | -------------------------------------------------- |
| `GET`  | `/api/health/`                | Fixed `{"status": "ok"}`; exposes no configuration  |
| `GET`  | `/api/twitch/oauth/start/`    | Redirects to Twitch; carries no secret              |
| `GET`  | `/api/twitch/oauth/callback/` | Exchanges the code, then redirects to the frontend  |
| `GET`  | `/api/twitch/connection/`     | Connection status; carries no token material        |

The health endpoint returns a constant. It deliberately does not report database
or broker reachability, versions, hostnames or environment values, because it is
reachable without authentication.

The connection endpoint returns a strict allowlist: `connected`, `account`
(id, login, display name), `scopes` and `requires_reauthorization`. Access
tokens, refresh tokens, the client secret, raw Twitch token responses and
authorization codes are never part of any response.

The callback returns the browser to the frontend with a short outcome flag such
as `?twitch=connected` or `?twitch=error&reason=invalid_state`. No token ever
reaches the browser, its URL, its storage or its JavaScript state.

### Data and messaging

- **PostgreSQL** is the primary datastore. The only product table is
  `twitch_twitchconnection`.
- **Redis** is the Celery broker and result backend, and also backs Django's
  cache, which holds the app access token and in-flight OAuth state.
- **Celery** is configured in `backend/clipperstash/celery.py` and exposes a
  single `clipperstash.ping` task used to confirm a worker is correctly wired.
  No periodic schedule and no monitoring work is registered.

### Frontend

`web/` is a Next.js App Router application in TypeScript with Tailwind CSS. It
serves one route, `/`, which shows the product name, the tagline, a disabled
streamer URL input representing the future entry point, a Twitch connection
section, and the live result of calling `GET /api/health/` through the typed
client in `web/src/lib/api.ts`. The backend base URL comes from
`NEXT_PUBLIC_API_BASE_URL`.

The Twitch section reads `GET /api/twitch/connection/` and offers a link to
`/api/twitch/oauth/start/`. It is a plain navigation, so the OAuth exchange
happens entirely between the backend and Twitch and no credential is available
to the frontend. The streamer input stays disabled: streamer resolution is a
later milestone.

There is no authentication, no routing beyond the single page, no component
framework and no animation library.

## 4. Planned high-level pipeline

### Monitoring and clipping pipeline

```
Twitch API access / OAuth         IMPLEMENTED
  -> stream / chat monitoring     NOT IMPLEMENTED
  -> moment scoring               NOT IMPLEMENTED
  -> Twitch clip                  NOT IMPLEMENTED
  -> ClipperStash dashboard       NOT IMPLEMENTED
```

Expanded, the V0 target pipeline is expected to become:

```
Streamer URL                      NOT IMPLEMENTED
  -> streamer resolution          NOT IMPLEMENTED
  -> live / offline detection     NOT IMPLEMENTED
  -> stream session               NOT IMPLEMENTED
  -> realtime Twitch chat ingestion   NOT IMPLEMENTED
  -> moment scoring               NOT IMPLEMENTED
  -> interesting moment detection NOT IMPLEMENTED
  -> Twitch clip creation         NOT IMPLEMENTED
  -> ClipperStash dashboard       NOT IMPLEMENTED
```

### Later authorized-media pipeline

```
authorized clip                   NOT IMPLEMENTED
  -> FFmpeg                       NOT IMPLEMENTED
  -> transcription                NOT IMPLEMENTED
  -> 9:16 rendered asset          NOT IMPLEMENTED
  -> captions                     NOT IMPLEMENTED
```

This second pipeline only ever operates on clips the operator is authorized to
use. It is not part of the current milestone and no media tooling is installed.

### How the foundation supports it

The pieces already in place map onto the pipeline as follows: the `twitch` app
owns API access and credentials today and will own EventSub and chat ingestion,
`streamers` and `monitoring` will own resolution and session state, `moments`
will own scoring, and `clips` will own clip records and the dashboard's read
side. Clip creation will use the `clips:edit` scope already being requested. Long-running and scheduled work will run on the existing
Celery worker against Redis, and all durable state will live in PostgreSQL.
