# ClipperStash

Turn live moments into clips automatically.

ClipperStash monitors a Twitch stream, identifies moments worth keeping, creates
Twitch clips and collects them in one dashboard. See
[`docs/architecture.md`](docs/architecture.md) for the product objective, the
current architecture and the planned pipeline.

**Status:** foundation only. None of the monitoring, detection or clipping
pipeline is implemented yet — the stages are listed as `NOT IMPLEMENTED` in the
architecture document.

## Repository layout

```
backend/             Django project and product apps
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

### 2. PostgreSQL and Redis

```bash
docker compose up -d
docker compose ps
```

This starts PostgreSQL on port 5432 and Redis on port 6379, using the values from
`.env`. Stop them with `docker compose down`, or `docker compose down -v` to also
discard the volumes.

### 3. Django

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

### 4. Celery worker

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

### 5. Next.js

```bash
cd web
npm install
npm run dev
```

The application runs at http://localhost:3000 and reports whether it can reach
`GET /api/health/` on the backend. Point it elsewhere by setting
`NEXT_PUBLIC_API_BASE_URL` in `web/.env.local`.

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
| `NEXT_PUBLIC_API_BASE_URL` | Backend base URL used by the frontend        |

Never commit real credentials, API keys or tokens.
