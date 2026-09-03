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

V0 is a working vertical slice, not a finished product. The current milestone
(Milestone 0) is narrower still: it establishes the repository, the runtime
services and the shape of the codebase, and deliberately implements none of the
product pipeline.

Milestone 0 delivers:

- A Django + Django REST Framework backend with the product's app boundaries
  created but empty.
- A `GET /api/health/` endpoint returning a fixed `{"status": "ok"}` payload.
- PostgreSQL, Redis and Celery configured entirely from environment variables.
- A Next.js + TypeScript + Tailwind application shell that reports backend
  connectivity.
- Docker Compose definitions for the two backing services used locally.

Explicitly out of scope for Milestone 0:

- Twitch integration of any kind, including OAuth, Helix API calls, EventSub and
  chat ingestion.
- Streamer URL resolution and live/offline detection.
- Moment scoring or detection.
- Clip creation, clip download or any media handling.
- FFmpeg, transcription and vertical rendering.
- Authentication, accounts, billing and social publishing.
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

| App          | Future responsibility                                       | Status          |
| ------------ | ----------------------------------------------------------- | --------------- |
| `streamers`  | Streamer identity, resolution from a URL, stored channels    | NOT IMPLEMENTED |
| `twitch`     | Twitch API/EventSub client and credential handling           | NOT IMPLEMENTED |
| `monitoring` | Stream sessions and realtime signal ingestion                | NOT IMPLEMENTED |
| `moments`    | Moment scoring and interesting-moment detection              | NOT IMPLEMENTED |
| `clips`      | Clip records, clip creation and the dashboard's read models  | NOT IMPLEMENTED |

Each app currently contains only its `AppConfig` and an empty `models` module.
No models, serializers, views, tasks or business logic exist yet.

Configuration is read from environment variables via `django-environ`, with a
local `.env` file loaded when present. There is a single settings module; a
larger per-environment hierarchy is not warranted at this size and can be
introduced when a deployment target actually exists.

### API surface

| Method | Path            | Response            | Notes                                        |
| ------ | --------------- | ------------------- | -------------------------------------------- |
| `GET`  | `/api/health/`  | `{"status": "ok"}`  | Fixed payload; exposes no configuration data |

The health endpoint returns a constant. It deliberately does not report database
or broker reachability, versions, hostnames or environment values, because it is
reachable without authentication.

### Data and messaging

- **PostgreSQL** is the primary datastore. There are no product tables yet; only
  Django's built-in migrations apply.
- **Redis** is the Celery broker and result backend.
- **Celery** is configured in `backend/clipperstash/celery.py` and exposes a
  single `clipperstash.ping` task used to confirm a worker is correctly wired.
  No periodic schedule and no monitoring work is registered.

### Frontend

`web/` is a Next.js App Router application in TypeScript with Tailwind CSS. It
serves one route, `/`, which shows the product name, the tagline, a disabled
streamer URL input representing the future entry point, and the live result of
calling `GET /api/health/` through the typed client in `web/src/lib/api.ts`. The
backend base URL comes from `NEXT_PUBLIC_API_BASE_URL`.

There is no authentication, no routing beyond the single page, no component
framework and no animation library.

## 4. Planned high-level pipeline

### Monitoring and clipping pipeline

```
Twitch
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
will own API access, `streamers` and `monitoring` will own resolution and session
state, `moments` will own scoring, and `clips` will own clip records and the
dashboard's read side. Long-running and scheduled work will run on the existing
Celery worker against Redis, and all durable state will live in PostgreSQL.
