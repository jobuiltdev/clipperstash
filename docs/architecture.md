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

This establishes *who* a channel is.

**Milestone 3 — live observation and stream sessions. IMPLEMENTED.**

- On-demand live/offline observation through Twitch `GET /helix/streams`.
- `StreamSession` lifecycle: opened on a live observation, closed on an offline
  one, with database-enforced single-live-session and unique-stream invariants.
- Explicit separation of "offline" from "could not determine".
- `POST /api/streamers/<id>/observe/` and a minimal frontend control.

This establishes *whether* a channel is broadcasting, one check at a time.
Nothing drives those checks on a schedule.

**Milestone 4 — chat ingestion. IMPLEMENTED.**

- The connected-user OAuth scope set now includes `user:read:chat`, with a
  capability check for connections granted before it existed.
- An EventSub **WebSocket** runtime: welcome, keepalive, notification, reconnect
  handoff and revocation.
- A `channel.chat.message` subscription created over Helix with the user token.
- Normalization into a narrow `ChatMessage`, with pseudonymized chatters and
  database-enforced deduplication.
- A foreground `monitor_chat` management command.

**Milestone 5 — moment detection. IMPLEMENTED.**

- A pure detector over stored chat: two comparison windows, four signal
  families, five weighted component scores and one total.
- An activity gate, a candidate threshold and a per-session cooldown.
- `MomentCandidate` persistence, recording the full working behind a finding.
- A `detect_moments` management command, including historical replay.

The detector decides *that* a moment happened.

**Milestone 6 — clip creation. IMPLEMENTED.**

- Twitch Create Clip with the connected user's token, and Get Clips to confirm
  the result actually exists.
- A freshness rule that keeps live clipping honest about what it captures.
- A transactional claim, so one moment produces at most one clip request.
- `Clip` persistence and the `CLIP_REQUESTED -> CLIP_CREATED / FAILED`
  transitions, with a `create_clip` management command.

That completes the V0 pipeline as a sequence of manual steps.

**Milestone 7 — read-only operator dashboard. IMPLEMENTED.**

- Six `GET` endpoints over what the pipeline has already recorded: an overview,
  the detector calibration, a streamer's sessions, one session, that session's
  moments and one moment.
- A derived clip display state that separates an unknown request outcome from a
  failure, without adding a column or writing anything back.
- A `/dashboard` area in the frontend with overview, streamer, session and
  moment views, each with explicit loading, empty and failure states.

The dashboard only observes. It performs no write, contacts no external service
and refreshes only when a person asks it to.

Every stage of the V0 pipeline described in these milestones exists and can be
run. **None of it runs by itself.** Chat ingestion, detection and clip creation are each started
explicitly by a person, and no stage invokes the next. That an operator can
execute a live Create Clip today is not automatic clipping, and this document
should not be read as claiming otherwise.

Explicitly still out of scope:

- Any recurring monitoring, scheduling or orchestration: no Celery Beat, no
  polling loop, no cron, no supervisor. Chat ingestion, detection and clip
  creation each run only when a person starts them, and none invokes the next.
- Automatic monitor -> detect -> clip orchestration, and any automatic or
  continuous detector scheduling.
- Detector calibration against real collected streams, which is Milestone 8's
  work. Today's weights, thresholds and saturation points are initial values.
- Create Clip From VOD and any historical clipping. Live Create Clip captures
  what is airing when the request arrives, so a moment that has passed cannot
  be clipped.
- Clip downloading, Get Clips Download, CDN scraping and any other media
  retrieval. Nothing is fetched from Twitch beyond API metadata.
- FFmpeg, transcription (Whisper or otherwise), captions and vertical
  rendering.
- Publishing anywhere: TikTok, Instagram, YouTube or any other destination.
- EventSub webhooks and Conduits; `stream.online` / `stream.offline`.
- Twitch IRC.
- Multi-stream scaling: socket pools, worker coordination, leader election.
- Automatic chat retention or deletion.
- Any model inference: no LLM, no sentiment API, no embeddings, and no audio or
  video analysis. Every signal is a count over text the pipeline already has.
- ClipperStash end-user authentication, accounts, sessions, permissions,
  billing, teams, quotas, multi-tenant authorization and social publishing. The
  dashboard is unauthenticated, single-tenant and read-only.
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
`backend/apps/`, split into the domains the pipeline needs:

| App          | Responsibility                                              | Status                  |
| ------------ | ----------------------------------------------------------- | ----------------------- |
| `streamers`  | Streamer identity, resolution from a URL, stored channels    | IMPLEMENTED (see below) |
| `twitch`     | Twitch API client, OAuth and credential handling             | IMPLEMENTED (see below) |
| `monitoring` | Stream sessions and realtime signal ingestion                | IMPLEMENTED (see below) |
| `moments`    | Moment scoring and interesting-moment detection              | IMPLEMENTED (see below) |
| `clips`      | Clip records and clip creation                               | IMPLEMENTED (see below) |
| `dashboard`  | Read-only presentation across every other app; no models     | IMPLEMENTED (see below) |

The EventSub transport lives in `twitch`, under `apps/twitch/eventsub/`;
`monitoring` owns the chat domain that consumes it, `moments` owns detection over
that chat, and `clips` owns turning a detected moment into a Twitch clip.
`dashboard` owns the read side and nothing else: it stores nothing, writes
nothing, and every domain app remains usable without it.

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

**Requested scopes.** Exactly two: `clips:edit`, for clip creation in a later
milestone, and `user:read:chat`, which Twitch requires on a **user** token to
receive `channel.chat.message` over an EventSub WebSocket. Nothing else is
requested — not `user:read:email`, and no bot, moderator or send-message scope,
none of which is needed merely to *receive* chat as the authorizing user.

**Capability, not deletion.** A connection authorized before `user:read:chat`
existed is still valid for what it was granted, so it is kept. Instead,
`TwitchConnection.missing_scopes()` reports the gap:
`GET /api/twitch/connection/` returns `requires_reauthorization: true` and
`capabilities.chat_read: false`, chat monitoring refuses to start, and
re-authorizing updates the same row in place with the new scopes.

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

### Live observation and stream sessions

`backend/apps/monitoring/` owns the observation lifecycle. As with `streamers`,
it builds no Twitch requests of its own.

| Module           | Responsibility                                           |
| ---------------- | -------------------------------------------------------- |
| `services.py`    | `observe_streamer()` and every lifecycle rule             |
| `models.py`      | `StreamSession`                                           |
| `serializers.py` | The safe live/offline representation                      |
| `exceptions.py`  | `StreamStateUnavailableError`                             |
| `views.py`       | `POST /api/streamers/<id>/observe/`                       |

**Lookup.** `twitch.services.lookup_stream_by_user_id()` calls
`GET /helix/streams?user_id=<id>` with an **app access token** from the
Milestone 1 Client Credentials cache. No connected-operator OAuth token is
involved and no additional scope is requested, so live checks work with no
Twitch account connected. The streamer's stored `platform_user_id` is used
directly — a live check never re-resolves the channel through Get Users. A
cached app token that Twitch rejects is minted once more and the lookup retried
exactly once.

**Session identity.** Twitch's `stream.id` is the identity of a broadcast.
`started_at` is Twitch's own authoritative value, parsed as timezone-aware UTC;
a start time that cannot be read makes the whole observation unusable rather
than being replaced with the local clock. `viewer_count`, `title`, `game_id`,
`game_name`, `language` and `is_mature` are persisted; `thumbnail_url` and tags
are parsed but deliberately not stored, and no follower counts or unrelated
broadcaster metadata are collected.

**Lifecycle.**

| Observation | Effect |
| ----------- | ------ |
| Live, no session for that stream id | Open a `StreamSession`, status `LIVE` |
| Live, same stream id | Reuse the session; refresh title, category, language, maturity, viewer count and `last_observed_at`; leave `started_at` untouched |
| Live, different stream id while one is live | Close the older session, then open or reuse the session for the new stream id |
| Offline (successful response, empty `data`) | Close the live session, if any |
| Offline with no live session | Do nothing |

Repeat observations are idempotent: a live check returns the same session, and a
second offline check leaves the already-recorded `ended_at` alone.

`Streamer` carries no live flag. An active session *is* the representation of an
active broadcast.

**`ended_at` is an observation, not a broadcast end.** Get Streams reports only
that a broadcaster is no longer live; it does not say when they stopped. So
`ended_at` records when ClipperStash *observed* the stream to be over, and is
only as precise as the gap between checks. It is never presented as Twitch's
exact end time.

**Offline is not the same as unknown.** Only a successful Twitch response with
no stream counts as an offline observation. A timeout, a 5xx, an authentication
failure, a malformed payload, a stream belonging to another broadcaster, or a
stream not marked `live` all raise `StreamStateUnavailableError` — the API
answers `503 stream_state_unavailable`, and **no session state is changed**.
"Could not determine" is never allowed to close a broadcast.

**Concurrency.** Two invariants are enforced by the database rather than by
application code: `platform_stream_id` is unique, and a partial unique
constraint permits at most one `LIVE` session per streamer while allowing any
number of ended ones. During a live observation the streamer row is locked with
`SELECT ... FOR UPDATE` inside a transaction, so two near-simultaneous checks of
the same channel are serialized; the constraint is the backstop if they are not.
No distributed lock is introduced.

**No scheduler.** Every check is explicitly requested. There is no Celery Beat
entry, periodic task or polling loop, and a test asserts the beat schedule is
empty. Choosing a monitoring cadence is deferred until the chat-ingestion
architecture is known.

### Chat ingestion

Twitch permits `channel.chat.message` over an EventSub WebSocket only with a
**user** access token carrying `user:read:chat`. The app token used for streamer
resolution and stream observation is not accepted here, so this is the one part
of the pipeline that depends on a connected operator account.

| Module                             | Responsibility                                   |
| ---------------------------------- | ------------------------------------------------ |
| `twitch/eventsub/messages.py`      | Envelope parsing; transport format only           |
| `twitch/eventsub/client.py`        | The socket itself, and the seam tests replace     |
| `twitch/eventsub/runtime.py`       | Welcome, keepalive, handoff, revocation, recovery |
| `twitch/client.py` + `services.py` | Creating the subscription over Helix              |
| `monitoring/chat.py`               | Normalization, pseudonymization, persistence      |
| `monitoring/chat_monitor.py`       | Preconditions and wiring                          |
| `monitoring/management/commands/monitor_chat.py` | The foreground entry point          |

**Subscription.** `POST /helix/eventsub/subscriptions` with type
`channel.chat.message`, version `1`, condition
`{broadcaster_user_id, user_id}` and transport
`{method: "websocket", session_id}`. `broadcaster_user_id` is the resolved
streamer's stable Twitch id and `user_id` the connected account's; logins are
never used as identity keys. A 401 triggers exactly one refresh and one retry,
as established in Milestone 1; a second rejection flags the connection for
re-authorization and stops.

**Socket lifecycle.** One connection to `wss://eventsub.wss.twitch.tv/ws`. The
subscription is created immediately on `session_welcome`, before any other frame
is read, because Twitch expects it promptly. The read timeout comes from the
`keepalive_timeout_seconds` Twitch sends, plus a small grace, rather than a
hardcoded interval. Nothing is ever sent to the socket: EventSub is
receive-oriented, and the library is configured with `ping_interval=None` so the
only outbound traffic is a protocol-level pong answering Twitch's own pings.

**Two kinds of reconnect**, deliberately distinguished:

- *Twitch handoff.* A `session_reconnect` names a `reconnect_url`, used exactly
  as supplied. Twitch carries the subscriptions to the replacement connection,
  so none is recreated, and the old socket is held open until the replacement
  has delivered its own welcome before being retired.
- *Ordinary loss.* The socket dies or falls silent past the keepalive window.
  There is no handoff, so a fresh connection to the base URL is opened and the
  subscription is recreated against the new session id.

Only those two URLs are ever dialled. No URL comes from user input.

**Lost events are lost.** Twitch does not replay chat missed during a
disconnected interval, and ClipperStash does not infer it. A gap in the stored
messages is a real gap.

**At-least-once delivery.** The same notification can arrive more than once.
Both `twitch_event_message_id` (the EventSub delivery) and `twitch_message_id`
(the chat message) are unique columns, so a repeat is refused by the database
rather than by an in-memory set — which also holds across restarts.

**What is kept.** Session, both ids, a chatter hash, the text, the timestamp and
an emote count. Badges, colours, profile data, subscription and reward metadata,
cheermotes, fragments and the raw payload are all discarded during
normalization. Emotes are counted from Twitch's own fragments, never by scanning
text for colon syntax. The text is retained solely because the moment detector
needs reaction language as evidence.

**Timestamp.** The EventSub `message_timestamp`, i.e. when Twitch produced the
notification — the closest authoritative delivery time this subscription type
offers, as `channel.chat.message` carries no separate sent-at field. Always
timezone-aware UTC; the local clock is never substituted.

**Pseudonymization.** The chatter's Twitch user id is HMAC-SHA256'd with
`CHAT_USER_HASH_SECRET` and the raw id is never stored or logged. A keyed
construction rather than a bare digest, because Twitch user ids are short
numeric strings an unkeyed hash would not protect. The key is environment-only,
never returned by an API, and deliberately separate from
`TWITCH_CLIENT_SECRET`. It answers exactly one question — how many distinct
chatters reacted.

**Session ownership.** Messages attach only to a **live** `StreamSession`, and
chat never creates one: stream observation remains the sole authority on stream
lifecycle. A notification whose broadcaster does not match the session's streamer
is rejected rather than filed. Shared Chat source metadata is ignored; the
subscription's target channel is the ownership boundary, and Shared Chat
analytics are deferred.

**Chat failure is not stream state.** Revocation, an authentication failure or a
dead socket stop ingestion, log a safe reason and — for revocation — mark the
connection as needing re-authorization. None of them alters the `StreamSession`,
and none deletes stored messages.

**Retention.** `ChatMessage` is short-lived operational data for detection, not
durable user history. Automatic expiry is **not** implemented:

```
automatic chat retention / deletion   NOT IMPLEMENTED
```

Nothing deletes these rows today; a later hardening milestone will add it.

**A run follows one broadcast.** The monitor consults the session's current
status before every receive and before replacing a socket, re-reading only the
status with a single indexed `exists()` query rather than trusting the object
loaded at startup. When stream observation ends the session, the run stops:
socket closed, no reconnect, no new subscription, no change to the session, no
chat deleted, and the connection is *not* flagged for re-authorization. It is a
normal stop, reported separately from revocation, authentication failure, socket
loss and keepalive timeout. Because the check rides the existing receive loop, a
keepalive is enough to notice — a quiet channel does not keep a finished run
alive — and no timer or extra polling loop is introduced. EventSub never decides
that a stream ended; Milestone 3 remains the sole authority, and chat only
observes what it decided.

**Runtime.** A foreground management command, `monitor_chat`, run by a person.
Nothing starts it automatically, no view spawns a socket thread, and there is no
Celery task or Beat entry — a test asserts the beat schedule is empty. How
continuous monitoring should be driven is deliberately deferred until the moment
detector's shape is known:

```
autonomous monitor orchestration   NOT IMPLEMENTED
```

### Moment detection

`backend/apps/moments/` scores the chat that ingestion stored. It is the only
part of the pipeline with no external dependency at all: no network, no model,
no service. Every signal is a count over data already in the database.

| Module                  | Responsibility                                         |
| ----------------------- | ------------------------------------------------------ |
| `detector/config.py`    | Every window, weight, threshold and saturation point    |
| `detector/window.py`    | `ChatSample`, window boundaries, splitting              |
| `detector/signals.py`   | Counting, and the reaction lexicon                      |
| `detector/scorer.py`    | Scaling counts into components and one total            |
| `detector/detector.py`  | `score_samples()` and the `MomentScore` result          |
| `services.py`           | Querying, cooldown, `MomentCandidate` persistence       |
| `models.py`             | `MomentCandidate`                                       |

**The detector is pure.** `score_samples()` takes chat samples and an explicit
evaluation time and returns a score. It performs no I/O, reads no clock and
never touches the ORM — it does not even see a `ChatMessage`, only a small
`ChatSample` carrying the four fields scoring reads. The same inputs always
produce the same answer, which is what makes replay and deterministic tests
possible. Everything that touches the database lives in `services.py`.

**Windows.** Two, ending at the evaluation time `T`, abutting exactly and never
overlapping:

```
baseline: (T - 70s, T - 10s]      60 seconds
current:  (T - 10s, T]            10 seconds
```

Both are half-open as `(start, end]`, and `baseline_end == current_start`, so a
message landing precisely on the shared instant belongs to the baseline and to
nothing else. A message at exactly `T` is current; one at exactly the baseline
start falls outside both. Naive timestamps are refused rather than guessed at.

**Signals.** Four families, all deterministic counts:

- *Message velocity.* Current rate against baseline rate. The baseline rate is
  floored, so silence yields a large but finite ratio rather than a division by
  zero. The ratio is then multiplied by a confidence term scaled on current
  volume — which is what stops "0 messages, then 1" reading as viral while "0
  messages, then 30" still does.
- *Unique chatter activity.* Distinct pseudonymous chatter hashes. Half the
  score is participation (chatters per message, so one person sending everything
  scores near zero) and half is absolute breadth.
- *Emote intensity.* From Twitch's own fragment counts, stored during ingestion.
  Half density, half coverage. No emote name is ever inspected.
- *Reaction language.* A small rule-based lexicon of tokens and phrases, matched
  against normalized whole tokens — never substrings, so "what" does not fire
  inside "whatever". Deliberate elongation is collapsed for listed words only,
  so "lollll" matches but "brrrr" is left alone. The reacting share is
  multiplied by reaction breadth, so one person typing "LMAO" twenty times
  cannot look like twenty people reacting once.

**Score.** Each component is scaled into 0–1 against a saturation point, then
weighted — velocity 0.40, reaction 0.20, diversity 0.15, emote 0.15, absolute
activity 0.10 — and published on a 0–100 scale. The weights sum to 1.0 and a
test asserts it. Every component is persisted alongside the total, so a
surprising score can always be explained by which part produced it.

**Two guards.** An activity gate (at least 5 messages from at least 3 distinct
chatters) excludes windows too quiet to conclude anything from, whatever the
arithmetic says; and a candidate threshold of 70 must then be cleared. A window
is always scored, even when the gate fails, because the diagnostics are useful
for calibration — only the verdict is withheld. After a candidate, a 45-second
cooldown keeps one burst to one row; the boundary is inclusive, so exactly 45
seconds later a new candidate may be recorded. Cooldown is a persistence
concern, so the pure detector never learns about it.

**All calibration is initial.** The windows, weights, saturation points,
thresholds and cooldown are in `detector/config.py` as one frozen
`DetectorConfig`, passed explicitly rather than read from globals. They were
chosen to be explainable, not optimal, and are expected to move once real
collected streams have been replayed.

**What a candidate holds.** Aggregates only: the window boundaries, the raw
counts for both windows, the five component scores and the total. No message
text, no message ids, no chatter identities and no raw payload are copied into
it, so a finding can be reviewed and recalibrated without carrying chat content
forward. `status` is always `DETECTED`; the clip-related members exist on the
column so it will not need migrating later, and nothing transitions to them.

**Replay.** The evaluation time is a parameter all the way down, so a past
window can be scored exactly as it was. The session's status is neither consulted
nor changed — an ended broadcast can be re-scored from its stored chat, which is
how threshold calibration will work.

**Queries.** One bounded read over `(session, timestamp)` — the index added with
chat ingestion — covering only the ~70 seconds both windows can contain, and
only the four columns scoring reads. Chat outside that span is never loaded, so
cost does not grow with the length of the broadcast. One further query checks
the cooldown, and one inserts a candidate.

**Runtime.** `manage.py detect_moments <session-id>`, optionally `--at <ISO>`
for replay and `--no-persist` to score without recording. Run by a person.
Nothing schedules it, and chat ingestion does not call it:

```
continuous detector scheduling        NOT IMPLEMENTED
automatic invocation from EventSub    NOT IMPLEMENTED
```

### Clip creation

`backend/apps/clips/` turns a detected moment into a real Twitch clip. Like the
rest of the pipeline it builds no Twitch requests of its own: transport stays in
the `twitch` app.

| Module              | Responsibility                                        |
| ------------------- | ----------------------------------------------------- |
| `config.py`         | The freshness budget and verification timings          |
| `models.py`         | `Clip`, and the controlled failure vocabulary          |
| `exceptions.py`     | Preconditions kept distinct from post-claim failures   |
| `services.py`       | Preconditions, the claim, the request, verification    |
| `management/commands/create_clip.py` | The manual entry point                |

**Create Clip is a user-token operation.** Twitch requires a user access token
carrying `clips:edit`; the app token used for resolution and stream observation
is not accepted. That scope is already part of the connection requested in
Milestone 1, so Milestone 6 adds no new scope. Only `broadcaster_id` is sent —
Twitch's stable id, never a login. A custom title is deliberately omitted
because it can be rejected by AutoMod, and a custom duration because there is no
evidence yet on which to tune one.

**202 does not mean created.** Twitch answers an accepted request with `202
Accepted` and a clip id. The clip may not exist yet, so nothing advances on the
strength of that alone: `GET /helix/clips?id=<id>` is polled until Twitch returns
the clip, and only then is it recorded. An empty result means "not ready", never
"failed". The URL stored is the one Twitch returns; it is never assembled from
the id, because a guessed link might not resolve.

The verification deadline is 60 seconds, polled every 2. Twitch's documentation
contradicts itself here — the Clips guide says to assume failure after 15
seconds, the API reference says 60 — and the longer bound is chosen so a clip
still being assembled is not abandoned. A test pins the value so changing it is
deliberate. Elapsed time is measured on the monotonic clock, so a system clock
adjustment mid-poll cannot move the deadline; the clock and sleeper are injected,
so the whole cycle runs instantly under test.

**Freshness is the load-bearing rule.** Live Create Clip captures what is airing
when the request arrives — it cannot reach back to the detected moment. A
candidate is therefore only eligible while at most 15 seconds old, inclusive at
the limit, with a two-second tolerance for clock skew in the other direction.
Anything older is refused: clipping it would capture an unrelated part of the
stream while claiming to be that moment. This is exactly why a candidate
produced by `detect_moments --at` is not eligible — replayed moments are for
calibration, and clipping a past moment precisely needs Twitch's VOD path, which
is NOT IMPLEMENTED.

**One clip per moment.** Clipping is claimed inside a short transaction: the
candidate row is locked with `SELECT ... FOR UPDATE`, its state re-read under
that lock, its single `Clip` created, and the status moved `DETECTED ->
CLIP_REQUESTED`. A second caller blocks on the lock and then sees the work is
already under way rather than sending its own request. The transaction closes
before any HTTP happens — holding one open across a minute of polling would pin
a connection for no reason. A `OneToOneField` and a unique `twitch_clip_id` make
the invariant the database's, not the service's.

**Two kinds of failure, kept apart.** A *precondition* — stale candidate, ended
session, ineligible status, missing or unscoped connection — means nothing was
claimed and nothing was sent, so the candidate is left exactly as it was. A
stale moment is not a broken one. A failure *after* the claim — a rejected
request, an unrecoverable authentication failure, an unreadable acceptance, a
verification timeout, a broadcaster mismatch — moves the candidate to `FAILED`
with a short controlled reason. A 401 refreshes the token once and retries once,
as everywhere else; a 403 is a channel clip restriction rather than a
credentials problem, so it never flags the connection for re-authorization.

**Stream state is untouched.** A clip request never changes a `StreamSession`,
and a Twitch 404 saying the broadcaster is not live proves nothing about it:
stream lifecycle remains entirely owned by stream observation, which is not
consulted over HTTP here at all.

**Recovery.** A candidate whose request Twitch accepted but whose run was
interrupted can be resumed: verification runs again against the stored clip id
with no second `POST`. A verification timeout keeps that id for exactly this
reason — a clip that appears after the deadline can still be found by hand.
There is no background retry.

**The limit of exactly-once.** Twitch's Create Clip accepts no
application-supplied idempotency key, so ClipperStash cannot ask whether a
request it already sent was received. The claim makes *concurrent* callers safe,
but a window remains between the claim committing and the clip id being stored.
A crash there — or a timeout that cannot distinguish "never arrived" from
"arrived and was accepted" — leaves a candidate `CLIP_REQUESTED` with a clip row
and no id.

That state is treated as indeterminate, not failed. It surfaces as
`clip_request_state_unknown`, and no further request is ever sent automatically
from it: re-sending could clip an unrelated part of the stream. The candidate is
not reverted to `DETECTED`, not marked `FAILED`, no id is invented, Get Clips is
not called without one, and nothing is deleted — the row is left exactly as it
is for an operator policy that is deliberately deferred.

The distinction is drawn on whether Twitch answered. A definitive response —
400, 403, 404, a second 401, or an accepted response with an unusable body — is
a known failure. A transport error is not, and is raised as a distinct
`TwitchTransportError` so it cannot be mistaken for one. The bounded 401 refresh
and single retry remains the only automatic re-send anywhere in this path, and
is safe precisely because Twitch rejected the first request outright. Nothing
else retries: the HTTP client itself is configured with no transport retries.

ClipperStash guarantees one local claim per moment and no automatic duplicate
request. It cannot guarantee Twitch did or did not act on a request whose
outcome it never learned, and does not pretend otherwise.

**What a clip holds.** The Twitch clip id, the URL Twitch returned, title,
duration, thumbnail, Twitch's creation time, when ClipperStash requested and
confirmed it, and a short failure code. No token, no raw response, no chat
content, no chatter identity — and deliberately not the `edit_url` Twitch
returns, which is a browser convenience ClipperStash neither automates nor
stores.

**Runtime.** `manage.py create_clip <candidate-id>`, optionally `--verify-only`.
Run by a person. Nothing chains the stages together:

```
automatic detector -> clip orchestration   NOT IMPLEMENTED
background clip worker                     NOT IMPLEMENTED
clip download / FFmpeg / captions          NOT IMPLEMENTED
VOD historical clipping                    NOT IMPLEMENTED
```

### API surface

| Method | Path                          | Notes                                              |
| ------ | ----------------------------- | -------------------------------------------------- |
| `GET`  | `/api/health/`                | Fixed `{"status": "ok"}`; exposes no configuration  |
| `POST` | `/api/streamers/resolve/`     | Resolves input to a persisted streamer              |
| `POST` | `/api/streamers/<id>/observe/` | One live/offline check; 503 when undeterminable    |
| `GET`  | `/api/twitch/oauth/start/`    | Redirects to Twitch; carries no secret              |
| `GET`  | `/api/twitch/oauth/callback/` | Exchanges the code, then redirects to the frontend  |
| `GET`  | `/api/twitch/connection/`     | Connection status; carries no token material        |
| `GET`  | `/api/dashboard/overview/`    | Installation counts and a bounded recent feed       |
| `GET`  | `/api/dashboard/detector-config/` | The detector calibration, read from the detector |
| `GET`  | `/api/streamers/<id>/sessions/` | Sessions for one streamer, paginated              |
| `GET`  | `/api/sessions/<id>/`         | One session with its moment and chat counts         |
| `GET`  | `/api/sessions/<id>/moments/` | That session's moments, filterable by clip state    |
| `GET`  | `/api/moments/<id>/`          | One moment with its full score working              |

The health endpoint returns a constant. It deliberately does not report database
or broker reachability, versions, hostnames or environment values, because it is
reachable without authentication.

The connection endpoint returns a strict allowlist: `connected`, `account`
(id, login, display name), `scopes`, `requires_reauthorization` and
`capabilities` (currently just `chat_read`). Access tokens, refresh tokens, the
client secret, the chat hashing key, raw Twitch token responses and
authorization codes are never part of any response.

The callback returns the browser to the frontend with a short outcome flag such
as `?twitch=connected` or `?twitch=error&reason=invalid_state`. No token ever
reaches the browser, its URL, its storage or its JavaScript state.

### Read-only dashboard

`apps/dashboard` holds every dashboard read: `state.py` (derived display
state), `queries.py` (querysets and aggregates), `serializers.py` (explicit
allowlists) and `views.py` (six `GET` endpoints). It defines **no models**, so
it adds no migration, and it is registered in `INSTALLED_APPS` only so its
package is importable and testable like any other app.

A separate app rather than views spread across the domain apps, for one reason:
the overview aggregates across streamers, sessions, moments and clips, and
belongs to none of them. Putting it in any single domain app would make that app
depend on the other three. The domain apps keep their own write-path
serializers untouched.

**Derived clip state.** The persisted `MomentCandidateStatus` cannot on its own
distinguish "a clip was requested and confirmed pending" from "a clip was
requested and nobody ever learned the outcome" — both are `CLIP_REQUESTED`. The
dashboard derives five display states from the candidate and its clip:

| State             | Derived from                                             |
| ----------------- | -------------------------------------------------------- |
| `not_requested`   | `DETECTED` or `REJECTED`                                  |
| `requested`       | `CLIP_REQUESTED` with a Twitch clip id                    |
| `request_unknown` | `CLIP_REQUESTED` with a clip row and no Twitch clip id    |
| `created`         | `CLIP_CREATED`                                            |
| `failed`          | `FAILED`                                                  |

These are presentation only. Nothing writes them back, and no persisted column
was added for them.

The overview's "recent verified clips" feed uses a stricter rule than any of
these display states: `CLIP_CREATED` **and** a recorded Twitch clip id **and** a
non-null `ready_at`, ordered by `ready_at` descending. All three are written by
the same verification step, so requiring them together means the feed shows a
clip only once Twitch has confirmed it exists. The existence of a `Clip` row is
deliberately not sufficient — M6 writes that row before the external request
precisely so it can represent a request whose outcome is unknown. Because
`ready_at` is non-null by that filter, PostgreSQL's nulls-first descending sort
cannot float an unconfirmed row above a confirmed one. The same predicates drive both the conditional-count
aggregates and the `clip_state` list filter, so a filtered list can never
disagree with the count that led an operator to it. They partition every
candidate exactly once, so summary cards sum to the total.

**Cost.** Every list selects the rows its serializer touches
(`select_related("clip", "session", "session__streamer")`), and every count is a
conditional aggregate in a single statement. Rendering fifty moments costs the
same number of queries as rendering one; tests assert the exact counts, and one
asserts that growing a session from four moments to twenty-nine does not change
them. Every list is bounded, with a default page of 50 and a hard ceiling of
200 regardless of the requested `limit`.

**What it cannot do.** Every view accepts `GET` alone, so DRF answers any other
method with 405 before application code runs. There is no serializer `create` or
`update`, no service call, no Twitch client and no detector import in the request
path. Tests assert all of this against every route at once: that each refuses
`POST`, `PUT`, `PATCH` and `DELETE`; that reading the whole surface leaves the
moment, clip and session tables byte-identical; and that no route makes an
outbound HTTP request, runs the detector or requests a clip, with each of those
patched to raise.

**What it will not publish.** The serializers are allowlists rather than
`__all__`, so a column added later is never exposed by accident. Chat text,
chatter hashes and Twitch message identifiers are absent from every response,
and chat appears only as a per-session message count. Tests assert the absence
of recognizable fixture chat text, the chatter hash, both message identifiers,
access and refresh tokens and the client secret from every endpoint's body.

### Data and messaging

- **PostgreSQL** is the primary datastore. The product tables are
  `twitch_twitchconnection`, `streamers_streamer`, `monitoring_streamsession`,
  `monitoring_chatmessage`, `moments_momentcandidate` and `clips_clip`.
- **Redis** is the Celery broker and result backend, and also backs Django's
  cache, which holds the app access token and in-flight OAuth state.
- **Celery** is configured in `backend/clipperstash/celery.py` and exposes a
  single `clipperstash.ping` task used to confirm a worker is correctly wired.
  No periodic schedule and no monitoring work is registered.

### Frontend

`web/` is a Next.js App Router application in TypeScript with Tailwind CSS. Its
entry route, `/`, shows the product name, the tagline, the streamer resolver, a
Twitch connection section, and the live result of calling `GET /api/health/`
through the typed client in `web/src/lib/api.ts`. The backend base URL comes
from `NEXT_PUBLIC_API_BASE_URL`.

The streamer box posts to `/api/streamers/resolve/` and renders the resolved
account: profile image, display name, `@username`, broadcaster type and a link
to the channel. A resolved streamer gains a "Check live status" control that
posts to `/api/streamers/<id>/observe/` once per press and renders live (with
title, category, viewer count and start time), offline, or — for a 503 — a
distinct "Status unknown" message. Neither control is gated on the Twitch
connection, because both run on the backend's app token. There is no polling and
no timer.

The Twitch section reads `GET /api/twitch/connection/` and offers a link to
`/api/twitch/oauth/start/`. It is a plain navigation, so the OAuth exchange
happens entirely between the backend and Twitch and no credential is available
to the frontend.

`/dashboard` is the operator's read-only view of the pipeline, with routes for
the overview, one streamer's sessions, one session and one moment. Each page is
a server component that validates its route id and hands it to a client
component; the client components fetch through the same typed client, which
models every dashboard response and uses no `any`.

Fetching goes through one small hook, `useResource`, which models a fetch as a
three-state union — loading, ready, failed — so "loaded but also failed" cannot
be represented. It has no timer, no interval and no subscription: a page loads
when it is opened and again when a person presses refresh. There is no
WebSocket from the backend to the frontend, no Server-Sent Events stream and no
background poll anywhere in the application.

Loading, empty and failure are treated as first-class states rather than
afterthoughts. Empty states say *why* they are empty — a quiet session
legitimately produces no moments — and failures render the backend's own
user-safe message with a retry control, never raw internals.

There is no authentication, no component framework and no animation library.

## 4. Planned high-level pipeline

### Monitoring and clipping pipeline

```
Twitch API access / OAuth         IMPLEMENTED
  -> streamer resolution          IMPLEMENTED
  -> live / offline observation   IMPLEMENTED
  -> chat ingestion               IMPLEMENTED (on demand)
  -> moment scoring               IMPLEMENTED (on demand)
  -> Twitch clip                  IMPLEMENTED (on demand)
  -> ClipperStash dashboard       IMPLEMENTED (read-only)
  -> recurring monitoring         NOT IMPLEMENTED
  -> automatic orchestration      NOT IMPLEMENTED
```

Expanded, the V0 target pipeline is expected to become:

```
Streamer URL                      IMPLEMENTED
  -> streamer resolution          IMPLEMENTED
  -> live / offline detection     IMPLEMENTED (on demand)
  -> stream session               IMPLEMENTED
  -> realtime Twitch chat ingestion   IMPLEMENTED (on demand)
  -> moment scoring               IMPLEMENTED (on demand)
  -> interesting moment detection IMPLEMENTED (on demand)
  -> Twitch clip creation         IMPLEMENTED (on demand)
  -> clip verification            IMPLEMENTED (on demand)
  -> ClipperStash dashboard       IMPLEMENTED (read-only)
  -> recurring monitoring         NOT IMPLEMENTED
  -> automatic orchestration      NOT IMPLEMENTED
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
`streamers` owns channel identity, `monitoring` owns live detection, session
state and chat ingestion, `moments` owns scoring over the stored chat,
`clips` owns clip creation, and `dashboard` owns the read side across every
other app. Clip creation uses the `clips:edit` scope already being requested, and
acts on a `Streamer` that has already been resolved. Long-running and scheduled
work will run on the existing Celery worker against Redis, and all durable
state will live in PostgreSQL.
