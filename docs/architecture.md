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
Twitch request.

**Milestone 2 — streamer resolution. IMPLEMENTED.**

- Normalization of a submitted Twitch channel URL or username to a login.
- Resolution of that login through Twitch Helix `GET /helix/users`.
- Persistence of the resolved account as a `Streamer`, idempotently.
- `POST /api/streamers/resolve/` and a minimal frontend form.

This establishes *who* a channel is. Nothing observes what it is doing.

Explicitly still out of scope:

- Live/offline detection, `GET /helix/streams` and stream sessions.
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
| `streamers`  | Streamer identity, resolution from a URL, stored channels    | IMPLEMENTED (see below) |
| `twitch`     | Twitch API client, OAuth and credential handling             | IMPLEMENTED (see below) |
| `monitoring` | Stream sessions and realtime signal ingestion                | NOT IMPLEMENTED         |
| `moments`    | Moment scoring and interesting-moment detection              | NOT IMPLEMENTED         |
| `clips`      | Clip records, clip creation and the dashboard's read models  | NOT IMPLEMENTED         |

Twitch EventSub and chat ingestion will also live in `twitch`, and are NOT
IMPLEMENTED. `monitoring`, `moments` and `clips` still contain only their
`AppConfig` and an empty `models` module.

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

### Streamer resolution

`backend/apps/streamers/` owns streamer-domain behavior. It does not build Twitch
requests: transport and authentication stay in the `twitch` app.

| Module           | Responsibility                                            |
| ---------------- | --------------------------------------------------------- |
| `parsers.py`     | Pure normalization of submitted input to a Twitch login    |
| `services.py`    | Resolution and idempotent persistence                      |
| `models.py`      | The `Streamer` record                                      |
| `serializers.py` | The request body and the safe public representation        |
| `exceptions.py`  | `StreamerInputError`, `StreamerNotFoundError`, `StreamerConflictError` |
| `views.py`       | `POST /api/streamers/resolve/`                             |

**Normalization.** `parsers.py` performs no I/O. It accepts a bare login or a
`twitch.tv` / `www.twitch.tv` channel URL over http or https, tolerating
surrounding whitespace, a trailing slash and any casing, and produces a
lower-cased login. It rejects other hosts, nested routes such as
`/videos/123456`, a known set of Twitch site routes (`directory`, `settings`,
`downloads`, `jobs`, `videos`, and similar), URLs carrying a query string or
fragment, non-http schemes, non-default ports, and logins that cannot be valid.
Host checks use `urllib.parse`, not string matching, so look-alikes like
`twitch.tv.evil.example/name`, `evil.example/twitch.tv/name` and
`twitch.tv@evil.example/name` are refused rather than resolved.

Login syntax is checked conservatively: lowercase ASCII letters, digits and
underscores, up to 25 characters. No minimum length is imposed. Twitch's current
registration rules are not a guarantee about accounts that already exist, so a
short but syntactically valid login is passed through to be looked up rather
than refused locally. Local rules only reject what cannot be a login at all;
Helix Get Users remains the authority on whether an account exists.

**Lookup.** `twitch.services.lookup_user_by_login()` calls
`GET /helix/users?login=<login>` with an **app access token** from the Milestone 1
Client Credentials cache. Public channel identity is app-level data, so
resolution deliberately does **not** require a connected operator's OAuth token
and works with no Twitch account connected at all. An empty `data` array is
Twitch's "no such user" and becomes a 404; a malformed payload is an integration
error. A cached app token that Twitch rejects is minted once more and the lookup
retried exactly once.

**No outbound request to the submitted URL.** The submitted value is parsed for
its host and path and then discarded. The backend never fetches it, never
follows a redirect from it and never downloads the profile image; only Twitch's
own OAuth and Helix hosts are contacted. That keeps a hostile submission from
steering an outbound request.

**Persistence.** `Streamer` records identity only — platform, the platform's
account id, username, display name, canonical channel URL, profile image URL,
broadcaster type, description and an active flag. Live status, titles,
categories, viewer and follower counts are deliberately absent; they belong to
stream monitoring. Two unique constraints apply: `(platform, platform_user_id)`
and `(platform, username)`.

**Idempotency.** Resolution matches on the platform account id, which is stable
across renames, so resolving the same channel twice refreshes the existing row
rather than creating a second one, and a renamed channel updates its stored
username and canonical URL in place. Mutable profile fields — display name,
profile image, broadcaster type, description — are refreshed on every
resolution. Twitch does release abandoned logins for re-registration; V0 does
not track username history, so if a stored login turns out to belong to a
different account the clash surfaces as a 409 rather than one record silently
adopting another's identity.

### API surface

| Method | Path                          | Notes                                              |
| ------ | ----------------------------- | -------------------------------------------------- |
| `GET`  | `/api/health/`                | Fixed `{"status": "ok"}`; exposes no configuration  |
| `POST` | `/api/streamers/resolve/`     | Resolves input to a persisted streamer              |
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

- **PostgreSQL** is the primary datastore. The product tables are
  `twitch_twitchconnection` and `streamers_streamer`.
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

The streamer box posts to `/api/streamers/resolve/` and renders the resolved
account: profile image, display name, `@username`, broadcaster type and a link
to the channel. It is not gated on the Twitch connection, because resolution
runs on the backend's app token.

The Twitch section reads `GET /api/twitch/connection/` and offers a link to
`/api/twitch/oauth/start/`. It is a plain navigation, so the OAuth exchange
happens entirely between the backend and Twitch and no credential is available
to the frontend.

There is no authentication, no routing beyond the single page, no component
framework and no animation library.

## 4. Planned high-level pipeline

### Monitoring and clipping pipeline

```
Twitch API access / OAuth         IMPLEMENTED
  -> streamer resolution          IMPLEMENTED
  -> stream / chat monitoring     NOT IMPLEMENTED
  -> moment scoring               NOT IMPLEMENTED
  -> Twitch clip                  NOT IMPLEMENTED
  -> ClipperStash dashboard       NOT IMPLEMENTED
```

Expanded, the V0 target pipeline is expected to become:

```
Streamer URL                      IMPLEMENTED
  -> streamer resolution          IMPLEMENTED
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
`streamers` owns channel identity today, `monitoring` will own live detection and
session state, `moments` will own scoring, and `clips` will own clip records and
the dashboard's read side. Clip creation will use the `clips:edit` scope already
being requested, and will act on a `Streamer` that has already been resolved. Long-running and scheduled work will run on the existing
Celery worker against Redis, and all durable state will live in PostgreSQL.
