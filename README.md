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

Python project managed with `uv`.

Install dependencies:

```bash
uv sync
```

Run instructions will be added when the first executable version is implemented.
