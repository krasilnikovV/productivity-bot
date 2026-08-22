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

Start the application:

```bash
uv run uvicorn productivity_bot.main:app --reload
```

Once the application is running, the available HTTP endpoints can be viewed in
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
