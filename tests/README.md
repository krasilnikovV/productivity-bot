# Test layout

Tests are grouped first by test type and then by the production symbol they cover.
The project uses three test types only: `unit`, `component`, and `integration`.

`tests/unit` covers domain and application behavior in isolation. Mock or fake
architectural dependencies such as repositories and external clients, but do
not mock small internal collaborators merely to isolate an implementation
detail.

`tests/component` covers an input adapter with its real framework runtime. For
example, FastAPI tests use `TestClient`, and Telegram tests use an aiogram
`Dispatcher`. The application use case behind the adapter is replaced with a
mock, fake, or stub. These tests cover routing, filters, validation,
dependency injection, and the mapping between framework and application data.

`tests/integration` covers an infrastructure adapter with a real technology or
a realistic external boundary, such as a PostgreSQL repository or an HTTP
client with a mock transport. A broader flow starting from a public entrypoint
belongs here only when it verifies an important composition risk that cannot be
covered reliably at narrower levels.

The path below `tests/unit` or `tests/component` mirrors the path below
`src/productivity_bot`.

For a class method, use the following structure:

```text
src/productivity_bot/application/use_cases/capture_task.py
└── CaptureTask.execute

tests/unit/application/use_cases/capture_task/capture_task/test_execute.py
```

The first `capture_task` directory represents the source module. The second
represents the `CaptureTask` class converted to snake case.

For a module-level function, omit the class directory:

```text
src/productivity_bot/adapters/singularity/mapper.py
└── map_task

tests/unit/adapters/singularity/mapper/test_map_task.py
```

Integration tests that intentionally cross several components are placed under
the public entrypoint that starts the scenario. Shared fixtures and helpers
live in the nearest common parent directory.

Every directory in the test tree is an explicit Python package with an empty
`__init__.py`. This gives repeated method-oriented filenames such as
`test_execute.py` unique import paths without pytest-specific import settings.
