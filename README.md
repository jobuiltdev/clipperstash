# ClipperStash

Turn live moments into clips automatically.

ClipperStash monitors a Twitch stream, identifies moments worth keeping, creates
Twitch clips and collects them in one dashboard. See
[`docs/architecture.md`](docs/architecture.md) for the product objective, the
current architecture and the planned pipeline.

**Status:** foundation, the Twitch API and OAuth layer, streamer resolution,
and on-demand live/offline observation. A Twitch channel can be resolved and
checked for a live broadcast, which opens and closes a stream session. Chat
ingestion, moment detection and clipping are not implemented yet, and there is
no recurring monitoring — those stages are listed as `NOT IMPLEMENTED` in the
architecture document.

## Repository layout

```
backend/             Django project, product apps and the Twitch integration
web/                 Next.js application shell
docs/                Project documentation
docker-compose.yml   PostgreSQL and Redis for local development
.env.example         Placeholder configuration
```

## Requirements

- Python 3.12+
- Node.js 20+
- Docker (for PostgreSQL and Redis)

## Setup

### 1. Configuration

```bash
cp .env.example .env
cp web/.env.example web/.env.local
```

Edit `.env` and set a local `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD`. Both
files are gitignored; only the `.env.example` templates are tracked, and they
contain placeholders only.

### 2. Twitch application

The Twitch integration needs a registered Twitch application. Nothing else in
the project requires it, and the automated tests never contact Twitch.

1. Sign in at https://dev.twitch.tv/console/apps and choose **Register Your
   Application**.
2. Give it any name, set the **OAuth Redirect URL** to exactly:

   ```
   http://localhost:8000/api/twitch/oauth/callback/
   ```

   The value must match `TWITCH_REDIRECT_URI` character for character, including
   the trailing slash, or Twitch rejects the authorization request.
3. Choose a category (Application Integration is a reasonable default) and
   register.
4. Copy the **Client ID**, then use **New Secret** to generate a client secret.
5. Put both into `.env`:

   ```
   TWITCH_CLIENT_ID=replace-with-your-twitch-client-id
   TWITCH_CLIENT_SECRET=replace-with-your-twitch-client-secret
   TWITCH_REDIRECT_URI=http://localhost:8000/api/twitch/oauth/callback/
   ```

The client secret is backend-only. It is never sent to the browser, never
returned by an API response, and must never be copied into a `NEXT_PUBLIC_*`
variable. `.env` is gitignored; only the placeholder `.env.example` is tracked.

#### Token handling

The user access token comes from Twitch's Authorization Code flow and is stored
on the backend. Requests use it as-is; the recorded expiry is kept as metadata
and is not what triggers a refresh. When Twitch answers a request with
`401 Unauthorized`, the backend refreshes the token once and retries the request
once. If that also fails, the connection is marked as needing re-authorization
rather than retried further.

Twitch's `/oauth2/validate` endpoint is supported and used to check a stored
token on demand. Twitch requires applications maintaining OAuth sessions to
validate at startup and hourly thereafter; ClipperStash does **not** yet do this
on a schedule. There is no recurring validation job in V0 — automating that
cadence is deferred to a later runtime-monitoring milestone.

To connect an account, start the backend and the frontend, then use **Connect
Twitch** on http://localhost:3000. The backend redirects to Twitch, Twitch
returns to the callback above, and the browser lands back on the frontend. The
only scope requested is `clips:edit`.

### 3. PostgreSQL and Redis

```bash
docker compose up -d
docker compose ps
```

This starts PostgreSQL on port 5432 and Redis on port 6379, using the values from
`.env`. Stop them with `docker compose down`, or `docker compose down -v` to also
discard the volumes.

### 4. Django

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements-dev.txt

python manage.py migrate
python manage.py runserver
```

The API is then available at http://127.0.0.1:8000.

```bash
curl http://127.0.0.1:8000/api/health/
# {"status": "ok"}
```

### 5. Celery worker

With Redis running and the backend virtualenv active, from `backend/`:

```bash
celery -A clipperstash worker --loglevel=info
```

On Windows the default prefork pool is unavailable; use the solo pool:

```bash
celery -A clipperstash worker --loglevel=info --pool=solo
```

To confirm the worker is wired up correctly, from a Django shell:

```bash
python manage.py shell -c "from clipperstash.celery import ping; print(ping.delay().get(timeout=10))"
# pong
```

No scheduled or monitoring tasks exist yet; `clipperstash.ping` is only a
configuration smoke test.

### 6. Next.js

```bash
cd web
npm install
npm run dev
```

The application runs at http://localhost:3000. Point it elsewhere by setting
`NEXT_PUBLIC_API_BASE_URL` in `web/.env.local`.

The page offers three things: a streamer box where a Twitch channel URL or
username resolves to a stored streamer and can then be checked for a live
broadcast, the Twitch connection control from an earlier milestone, and a
backend reachability indicator driven by `GET /api/health/`.

The live check runs only when the button is pressed. A failed check is shown as
"Status unknown", distinct from "Offline".

## API

| Method | Path                             | Purpose                                     |
| ------ | -------------------------------- | ------------------------------------------- |
| `GET`  | `/api/health/`                   | Liveness check                              |
| `POST` | `/api/streamers/resolve/`        | Resolve a Twitch channel URL or username    |
| `POST` | `/api/streamers/<id>/observe/`   | Check whether that streamer is live now     |
| `GET`  | `/api/twitch/oauth/start/`      | Begins the Twitch Authorization Code flow   |
| `GET`  | `/api/twitch/oauth/callback/`   | Twitch redirect target; exchanges the code  |
| `GET`  | `/api/twitch/connection/`       | Safe status of the connected Twitch account |

None of these responses contain tokens, the client secret or an authorization
code.

### Resolving a streamer

```bash
curl -X POST http://127.0.0.1:8000/api/streamers/resolve/ \
  -H "Content-Type: application/json" \
  -d '{"input": "https://www.twitch.tv/shroud"}'
```

Accepted input is a Twitch channel URL (`https://www.twitch.tv/name`,
`twitch.tv/name`, with or without `www`, `http`, or a trailing slash) or a bare
username. Input is trimmed and lower-cased. Twitch site pages such as
`/directory`, `/settings` or `/videos/123` are rejected, as are other hosts.

| Outcome                     | Status | `error.code`            |
| --------------------------- | ------ | ----------------------- |
| Resolved                    | 200    | —                       |
| Not a Twitch channel input  | 400    | `invalid_streamer_input` |
| No such Twitch account      | 404    | `streamer_not_found`    |
| Username held by another id | 409    | `streamer_conflict`     |
| Twitch unconfigured         | 503    | `twitch_not_configured` |
| Twitch unreachable          | 503    | `twitch_unavailable`    |

Resolution uses the backend's **app access token**, so it works whether or not a
Twitch account has been connected — connecting is only needed for actions taken
on an operator's behalf later. The backend never fetches the URL you submit: it
parses out the channel name and asks Twitch's Helix API directly.

### Checking live status

Take the `id` from a resolve response and ask whether that channel is broadcasting:

```bash
curl -X POST http://127.0.0.1:8000/api/streamers/1/observe/
```

While live:

```json
{
  "status": "live",
  "streamer": { "id": 1, "username": "shroud", "display_name": "shroud" },
  "stream": {
    "session_id": 10,
    "platform_stream_id": "41375541868",
    "started_at": "2026-09-04T09:00:00Z",
    "title": "Ranked grind",
    "category_id": "509658",
    "category_name": "Just Chatting",
    "language": "en",
    "viewer_count": 1200,
    "is_mature": false
  }
}
```

While offline, `status` is `"offline"` and `stream` is `null`.

| Outcome                        | Status | `error.code`              |
| ------------------------------ | ------ | ------------------------- |
| Live or offline determined      | 200    | —                         |
| Streamer id not resolved        | 404    | `streamer_not_found`      |
| Twitch unconfigured             | 503    | `twitch_not_configured`   |
| State could not be determined   | 503    | `stream_state_unavailable` |

A 503 is **not** an offline answer. If Twitch times out, errors, or replies with
something unreadable, ClipperStash reports that it does not know and leaves any
open session exactly as it was. Only a successful Twitch response containing no
stream closes a session.

Each request is one check. There is no background polling and no scheduler; how
continuous monitoring should be driven is deferred until chat ingestion is
designed.

## Checks

Backend (from `backend/`, with the virtualenv active):

```bash
pytest
ruff check .
ruff format --check .
python manage.py check
```

Frontend (from `web/`):

```bash
npm run lint
npm run typecheck
npm run build
```

## Configuration reference

All backend configuration comes from environment variables; see `.env.example`
for the full list.

| Variable                 | Purpose                                        |
| ------------------------ | ---------------------------------------------- |
| `DJANGO_SECRET_KEY`      | Django secret key                              |
| `DJANGO_DEBUG`           | Enable Django debug mode                       |
| `DJANGO_ALLOWED_HOSTS`   | Comma-separated allowed hosts                  |
| `POSTGRES_*`             | Database name, user, password, host and port   |
| `REDIS_URL`              | Redis connection URL                           |
| `CELERY_BROKER_URL`      | Celery broker, defaults to `REDIS_URL`         |
| `CELERY_RESULT_BACKEND`  | Celery result backend, defaults to `REDIS_URL` |
| `CORS_ALLOWED_ORIGINS`   | Browser origins allowed to call the API        |
| `FRONTEND_BASE_URL`      | Where OAuth returns the browser                |
| `TWITCH_CLIENT_ID`       | Twitch application client ID                   |
| `TWITCH_CLIENT_SECRET`   | Twitch application client secret (backend-only)|
| `TWITCH_REDIRECT_URI`    | Registered Twitch OAuth redirect URL           |
| `NEXT_PUBLIC_API_BASE_URL` | Backend base URL used by the frontend        |

Never commit real credentials, API keys or tokens.
