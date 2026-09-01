# Productivity Bot

Personal productivity assistant built around Telegram, Singularity, Calendar, and an LLM.

Telegram is the main interface. Singularity stores tasks and projects. Calendar events are used as time constraints. The backend handles task capture, task organization, and next-action selection.

The main interaction is simple:

```text
What should I do next?
        ↓
one concrete action
```

The goal is to reduce the amount of manual task-manager maintenance and avoid choosing from long task lists.

## Status

Early MVP development.

## Docs

* [Product](docs/product.md)
* [Architecture](docs/architecture.md)

## Time zone

The bot treats task dates without an explicit time zone as local time. The
default is `Europe/Moscow`. To use another time zone, set `USER_TIMEZONE` in
`.env` to an IANA name such as `Asia/Tbilisi`.

Restart the Telegram update worker after changing this setting.

## Development

The project requires Python 3.13 or newer and uses `uv` for dependency management.

Install dependencies:

```bash
uv sync
```

Start the development PostgreSQL instance:

```bash
docker compose up -d --wait postgres
```

The database stores its data in `tmpfs`. Stopping the container removes all
development data; the Compose configuration does not create a persistent volume.

Create a local environment file and fill in the required values:

```bash
cp .env.example .env
```

Apply database migrations:

```bash
uv run alembic upgrade head
```

Start the HTTP webhook receiver:

```bash
uv run productivity-bot
```

The worker starts processing all `pending` inbox rows immediately, so verify
that they are safe to process before its first startup.

Start the Telegram update worker in another terminal:

```bash
uv run productivity-bot-telegram-update-worker
```

Both processes are required for full operation. The HTTP process validates and
durably stores Telegram updates, while the worker processes the stored updates
and calls Singularity. An HTTP 200 response from the webhook means that an update
was committed to PostgreSQL; it does not mean that processing has completed.
Run both processes under a supervisor outside local development so a failed
process is restarted independently.

Once the HTTP process is running, the available endpoints can be viewed in
the Swagger UI at <http://127.0.0.1:8000/docs>.

Run the checks:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

Stop the development services:

```bash
docker compose down
```
