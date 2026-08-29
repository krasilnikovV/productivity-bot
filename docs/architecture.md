# Architecture

## Overview

The backend connects Telegram, Singularity, Calendar, PostgreSQL, and an LLM.

```text
Telegram
   |
   v
Productivity Bot
   |
   +-- Singularity
   +-- Calendar
   +-- PostgreSQL
   +-- LLM
```

Telegram is the main user interface.

Singularity owns task data.

PostgreSQL stores state specific to the bot.

Calendar provides time constraints.

The LLM is used where natural language understanding or generation is required.

The application follows Clean Architecture principles with a Ports and Adapters approach. Business logic should not depend on Telegram, FastAPI, Singularity, PostgreSQL, or a specific LLM provider.

## Application structure

```text
src/productivity_bot/
├── domain/
│   ├── entities/
│   ├── value_objects/
│   ├── services/
│   └── exceptions.py
│
├── application/
│   ├── use_cases/
│   ├── ports/
│   ├── dto/
│   ├── services/
│   └── exceptions.py
│
├── adapters/
│   ├── singularity/
│   ├── postgres/
│   ├── calendar/
│   └── llm/
│
├── entrypoints/
│   ├── http/
│   │   ├── routers/
│   │   └── schemas/
│   └── telegram/
│       ├── handlers/
│       └── keyboards/
│
├── bootstrap/
│   ├── container.py
│   └── application.py
│
├── config.py
└── main.py
```

Directories should be added when they are needed. The structure is a boundary definition, not a requirement to create empty modules in advance.

## Dependency rules

Dependencies point inward:

```text
entrypoints ──> application ──> domain
                    ^
                    |
                 adapters
```

`domain` contains business concepts and rules that do not require I/O.

`application` contains use cases and defines the interfaces it needs to communicate with external systems.

`adapters` implement those interfaces.

`entrypoints` translate HTTP, Telegram, or other external input into application calls.

`bootstrap` creates concrete implementations and wires them together.

`domain` and `application` must not import FastAPI, aiogram, SQLAlchemy, HTTP clients, or concrete integrations.

Application code must not call Singularity, PostgreSQL, Calendar, or an LLM directly.

## Domain

The domain contains concepts and rules that belong to the productivity system itself.

Examples may include:

* tasks used by the decision engine;
* task identifiers;
* time intervals;
* ranking rules.

Domain models should contain only the data needed by our business logic. They should not mirror Singularity API responses.

Plain Python types, dataclasses, enums, and value objects are preferred unless another dependency has a clear benefit.

## Application

The application layer contains the main use cases and coordinates domain logic with external dependencies.

Initial use cases are:

```text
CaptureTask
GetNextAction
CompleteTask
SkipTask
SnoozeTask
DecomposeTask
```

A use case may load data through application ports, apply business rules, update external state, and return a result.

It must not depend on how those operations are implemented.

For example, `GetNextAction` may use:

```text
TaskRepository
CalendarPort
RuntimeStateRepository
        |
        v
DecisionEngine
        |
        v
one task
```

The main ranking logic should remain deterministic and testable. The LLM may be used as a tie-breaker, but basic scheduling and ranking rules should stay in normal code.

## Ports

Ports are interfaces defined by the application layer.

Expected ports include:

```text
TaskRepository
RuntimeStateRepository
CalendarPort
LLMPort
```

For example, task operations are exposed through `TaskRepository`.

```text
Application
    |
    v
TaskRepository
    ^
    |
SingularityTaskRepository
```

The application therefore knows how to work with tasks, but does not know how the Singularity API works.

The same rule applies to PostgreSQL, Calendar, and LLM integrations.

## Adapters

Adapters contain integration-specific code and implement application ports.

### Singularity

The Singularity adapter is responsible for reading and updating tasks, projects, tags, and related entities.

A typical structure is:

```text
adapters/singularity/
├── client.py
├── schemas.py
├── mapper.py
└── adapter.py
```

`client.py` handles HTTP, authentication, timeouts, and API errors.

`schemas.py` contains Singularity request and response models.

`mapper.py` converts Singularity models into application or domain models and back.

`adapter.py` implements the application port.

Singularity-specific response models and HTTP details must not leak into application code.

### Calendar

The Calendar adapter provides events and busy intervals.

Calendar data is used as a constraint when selecting the next task.

The application should not depend on a specific calendar provider.

### LLM

The LLM adapter provides operations such as:

* parsing natural-language task input;
* classifying a task;
* decomposing a task;
* generating clarification questions;
* breaking ranking ties.

LLM output must be validated before it changes external state.

The application should not depend on a specific LLM provider.

### PostgreSQL

The PostgreSQL adapter contains SQLAlchemy-specific code.

SQLAlchemy models are database models and are not used directly as domain or
application models.

PostgreSQL must not become a second task database.

## Entrypoints

Entrypoints are responsible only for transport-specific concerns.

The main entrypoints are FastAPI and Telegram.

They:

1. receive external input;
2. convert it into an application command;
3. call a use case;
4. convert the result into a transport-specific response.

They must not contain task ranking, database access, Singularity API calls, or
other business rules.

Transport models should not be passed directly into application logic.

### Telegram authorization

Until linked Telegram accounts are stored in PostgreSQL, the current MVP uses the
required `TELEGRAM_ALLOWED_USER_IDS` setting as an account allowlist. Telegram
handlers that mutate state accept only private-chat messages whose sender ID is
in this allowlist. Messages without a sender and messages from groups,
supergroups, or channels are not passed to application use cases.

### Telegram webhook delivery

The current MVP durably accepts Telegram updates in this order:

1. authenticate the request and validate the update;
2. atomically insert the raw payload under its Telegram `update_id`, or detect a
   duplicate;
3. finish the insert transaction;
4. return an empty HTTP 200 response.

A repeated `update_id` receives HTTP 200 without running the handler again. An
insert or commit failure propagates as HTTP 500. HTTP 200 means durable
acceptance, not completed processing. The webhook does not call the aiogram
dispatcher and does not create an in-memory processing task.

The HTTP receiver and update worker run as separate operating-system processes.
The HTTP process owns FastAPI, webhook registration, a Telegram bot session, and
its PostgreSQL engine. The worker process separately owns the dispatcher,
handlers, application use cases, Singularity client, Telegram bot session, and
its PostgreSQL engine. Restarting or scaling one process type does not change the
lifecycle or concurrency of the other.

Each worker process starts a fixed number of processing loops. Every loop claims
at most one ready `pending` row using `FOR UPDATE SKIP LOCKED`, commits the claim,
and processes that update before requesting another one. External calls do not
run inside the claim transaction. Total processing concurrency is the configured
per-process concurrency multiplied by the number of worker processes. Updates
may complete in a different order from their arrival order.

The worker parses the stored payload and routes it through aiogram. Unsupported,
ignored, and authorization-rejected updates are consumed as `succeeded` without
an external mutation marker. A mutating handler completes authorization and
deterministic input checks before it commits
`external_mutation_started_at`, immediately before calling the application use
case that can mutate Singularity. If the marker cannot be committed, the use case
is not called and the attempt is safely rescheduled when possible.

The Singularity adapter reports whether a mutation is known not to have been
applied, is confirmed, or has an unknown outcome. A known rejection is terminal
`failed`; a proven pre-send transport failure can return to `pending`; and an
unknown non-idempotent outcome becomes `uncertain`. A confirmed mutation is
recorded as `succeeded` before a returned Telegram method is sent. Telegram reply
delivery is best effort, and a reply failure does not change the terminal inbox
state. Every transition includes the current `attempt_count`, so an old worker
cannot overwrite a recovered or reclaimed attempt. Transient failures while
persisting a known processing result are retried with the same attempt fence;
the worker does not discard the result and continue claiming updates.

Recovery runs once during worker startup and then at the configured interval. An
expired `processing` claim without a mutation marker returns to `pending`; one
with a marker becomes `uncertain`. Fresh claims are unchanged. An initial recovery
failure terminates the worker process but does not stop the HTTP process from
accepting updates.

During shutdown, the worker stops taking new claims and wakes idle loops. It lets
in-flight handlers finish only within the configured grace period, then cancels
the remaining processing loops before the dispatcher, shared HTTP clients, and
database engine are closed. A cancelled marked attempt remains `processing` for
the next recovery pass.

The HTTP and worker processes must run under external supervision. HTTP health
describes only the web process, so worker liveness is monitored separately. Queue
monitoring must cover pending depth, oldest pending age, and the number of
`uncertain` updates. Worker replicas and web replicas each create independent
PostgreSQL connection pools.

Automatic retry is allowed only when the external request is known not to have
been sent, the operation is read-only, or the external mutation has a verified
idempotency guarantee. If a non-idempotent mutation may have succeeded but its
result is unknown, the update is marked `uncertain` and is not retried blindly.
The same rule applies when recovering an abandoned worker claim after a crash.
PostgreSQL deduplicates Telegram delivery but cannot make a remote Singularity
mutation part of the same transaction.

The processing guarantees and failure policy are defined in
[ADR 0001](decisions/0001-use-postgresql-for-durable-telegram-update-processing.md).

Before the first rollout of this worker, the inbox must contain no legacy
`pending` rows created by the previous in-memory implementation. Such rows may
already have performed an external mutation. They require manual review; the
worker does not migrate them automatically.

For example:

```text
Telegram update
      |
      v
CaptureTaskCommand
      |
      v
CaptureTask
      |
      v
CaptureTaskResult
      |
      v
Telegram response
```

Pydantic is appropriate for HTTP schemas, external API schemas, configuration, and structured LLM output. Domain models do not need to depend on Pydantic.

## Dependency injection

Dependencies are passed explicitly, primarily through constructor injection.

A DI framework is not required at this stage.

Concrete implementations are created in the bootstrap layer:

```text
Settings
   |
   v
SingularityClient
   |
   v
SingularityTaskRepository
   |
   v
CaptureTask
```

This is the composition root of the application.

`main.py` should stay small. It creates the application and starts the configured entrypoints; it does not contain endpoints or business logic.

## Data ownership

### Singularity

Singularity is the source of truth for tasks.

It stores:

```text
tasks
projects
subprojects
subtasks
tags
dates
deadlines
priority
duration
recurrence
completion state
```

Task data should not be copied into PostgreSQL as a second source of truth.

### PostgreSQL

PostgreSQL stores state that does not belong in Singularity.

Expected data includes:

```text
users

telegram_accounts
    user_id
    telegram_user_id
    is_main

task_runtime_state
    user_id
    singularity_task_id
    snoozed_until
    rejection_count
    last_offered_at

user_preferences
interaction_history

telegram_update_inbox
    update_id
    payload
    status
    attempt_count
    available_at
    last_error
```

The schema can change as implementation details become clearer.

Development PostgreSQL runs in Docker Compose with its data directory mounted as
`tmpfs`. It does not use a bind mount or persistent Docker volume. Production
PostgreSQL uses durable storage.

### Calendar

Calendar events stay in the calendar provider.

The bot reads only the information required for planning and task selection.

## Main flows

### Capture

```text
Telegram message
      |
      v
interpret
      |
      v
clarify if needed
      |
      v
create task in Singularity
      |
      v
reply
```

### Next action

```text
request
   |
   v
load Singularity tasks
   |
   v
filter v0 candidates
   |
   v
rank v0 candidates
   |
   v
return one task
```

#### Temporary v0 selection policy

This policy is temporary. It applies only until the initial `/next`
implementation is superseded by a deliberately expanded selection policy.

Use one selection-time reference for the whole selection. A task is a candidate
only when it is active and its `start` is absent or not later than that
reference. Exclude a task whose `start` is later than the reference before
ranking.

A candidate whose deadline is earlier than the selection-time reference is
overdue. Every overdue candidate ranks ahead of every non-overdue candidate.
Within each of those two groups, rank candidates by these keys in order:

1. priority: `high`, then `normal`, then `low`;
2. nearest deadline to the selection-time reference;
3. earlier start;
4. ascending task ID.

A present deadline sorts before a missing deadline, and a present start sorts
before a missing start. The task-ID comparison is the final tie-breaker, so the
policy always selects one repeatable task.

The v0 policy does not consider calendar constraints, task duration, runtime
state, skip or snooze state, or LLM input or tie-breaking.

### Complete

Complete the task in Singularity.

### Skip

Record the rejection in runtime state and select another candidate.

A skip does not change the task itself.

### Snooze

Store temporary snooze state in PostgreSQL.

Do not change the task deadline just because the user does not want to do it right now.

### Decomposition

Use the LLM to produce a smaller actionable structure.

The resulting subtasks are stored in Singularity.

## Testing

The architecture should allow most business logic to be tested without network access or a running database.

```text
tests/
├── unit/
│   ├── domain/
│   └── application/
├── integration/
│   ├── adapters/
│   └── entrypoints/
└── 
```

Unit tests cover domain rules and application use cases.

Application tests should use small fake implementations of ports where practical.

For example:

```text
CaptureTask
    +
FakeTaskRepository
```

This is preferred over mocking HTTP or SQLAlchemy inside application tests.

Integration tests cover boundaries such as:

* Singularity API mapping and client behavior;
* PostgreSQL repositories;
* FastAPI endpoints;
* Telegram webhook handling.

Mocks are still useful where interaction itself is what the test needs to verify, but tests should not depend unnecessarily on implementation details.

## Background jobs

Later versions may run background jobs for:

* morning briefs;
* deadline checks;
* overdue tasks;
* repeatedly skipped tasks;
* task cleanup.

Background jobs call the same application use cases and ports as interactive entrypoints. They should not introduce a separate path to business logic.

They should avoid notifying the user unless user input is actually needed.

## Design guidelines

Use an abstraction when it protects an application boundary or isolates an external dependency.

Patterns that fit the current design include:

* Repository for task and runtime-state access;
* Adapter for external integrations;
* constructor-based Dependency Injection;
* Mapper between external and internal models;
* Strategy when multiple ranking implementations are actually needed;
* Factory for application or client construction.

Do not add architectural patterns only for consistency with a textbook. CQRS, event buses, domain events, Unit of Work, or similar abstractions should be introduced only if a concrete requirement makes them useful.

## Constraints

* Singularity is the task source of truth.
* PostgreSQL must not duplicate task data.
* Telegram is the primary UI.
* Proactive messages go to the main Telegram account.
* Next-action requests work from every linked Telegram account.
* External systems are accessed through adapters.
* Application code depends on ports, not concrete integrations.
* Business logic stays outside FastAPI and Telegram handlers.
* LLM output is validated before changing external state.
* Most application behavior must be testable without external services.
