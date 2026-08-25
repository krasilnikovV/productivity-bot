# ADR 0002: Run the Telegram update worker as a separate process

Date: 2026-08-25

Status: Accepted

## Context

[ADR 0001](/docs/decisions/0001-use-postgresql-for-durable-telegram-update-processing.md) defines PostgreSQL as the durable inbox for Telegram updates. It does
not decide whether the webhook receiver and the inbox consumer should run in the
same operating-system process.

The webhook and the worker have different responsibilities and lifecycle needs.
The webhook authenticates and validates an update, commits it to PostgreSQL, and
returns HTTP 200. The worker claims committed updates, invokes application use
cases, calls external services, persists processing outcomes, and sends Telegram
replies.

Running both roles in every HTTP process couples worker concurrency and shutdown
to the number and lifecycle of HTTP server processes. It also means that a web
deployment can interrupt in-flight processing. Keeping them in one process is
operationally simpler, but it obscures the boundary between durable acceptance
and asynchronous processing.

PostgreSQL already provides the coordination needed by multiple worker processes
through atomic claims with `FOR UPDATE SKIP LOCKED`. Adding a message broker only
to separate the two processes would duplicate durable-delivery infrastructure.

## Decision

The Telegram webhook receiver and the Telegram update worker will run as
separate processes. They may use the same application artifact and configuration,
but they have separate entrypoints and lifecycles.

The HTTP process owns:

* Telegram webhook authentication and payload validation;
* insertion into the PostgreSQL inbox;
* the HTTP response;
* Telegram webhook registration when configured.

The worker process owns:

* inbox claiming and abandoned-claim recovery;
* the aiogram dispatcher and Telegram handlers;
* application use-case execution;
* calls to Singularity and other processing integrations;
* persistence of processing outcomes;
* outbound Telegram replies produced by processing.

Webhook acceptance depends on a successful PostgreSQL commit, not on worker
liveness. HTTP 200 means that the update was durably accepted. It does not mean
that processing or a Singularity mutation succeeded. If no worker is available,
accepted updates remain in the inbox until processing resumes.

The current MVP will use PostgreSQL-backed workers directly. It will not add
RabbitMQ, Celery, or another task broker. A worker may be replicated; database
claiming prevents two workers from owning the same processing attempt.

An `uncertain` outcome is reserved for a non-idempotent Singularity mutation that
may have succeeded but whose result cannot be established safely. Validation,
unsupported input, read-only preprocessing, and failures known to occur before a
mutation could be sent must not become `uncertain`. A confirmed Singularity
success also must not become `uncertain` only because the later Telegram reply
failed.

The durable mutation marker must therefore be recorded at the Singularity
mutation boundary, not before dispatching the whole Telegram update. A crash can
still occur between recording the marker and sending the request. Without a
remote idempotency guarantee, this narrow ambiguity cannot be eliminated safely.

For the current MVP, `uncertain` updates are not retried automatically. They are
reviewed and resolved manually. A future decision may adopt a verified
Singularity idempotency mechanism and allow safe automatic retry.

## When to reconsider RabbitMQ

RabbitMQ should be reconsidered only when concrete requirements cannot be met
reasonably by the PostgreSQL inbox and direct workers. Relevant signals include:

* measured claim or polling load that harms the primary PostgreSQL workload;
* sustained queue latency or throughput that cannot be addressed by indexing,
  batching, or adding PostgreSQL-backed workers;
* a need for several independently scaled consumer groups, fan-out delivery, or
  routing rules that would make the inbox implementation materially complex;
* operational requirements for broker-specific delivery, backpressure, or queue
  isolation;
* an established managed RabbitMQ platform that reduces, rather than adds,
  operational cost.

Audio processing, multiple worker processes, delayed retries, or bounded
concurrency alone do not require RabbitMQ.

If RabbitMQ is introduced while PostgreSQL remains the durable acceptance store,
publishing must not rely on an unsafe database-and-broker dual write. A
transactional outbox or another explicitly documented delivery protocol will be
required. RabbitMQ will not remove the need for update deduplication, mutation
idempotency, or `uncertain` outcome handling. Celery may be considered separately
as a worker framework, but it is not required to use RabbitMQ and does not solve
these consistency problems.

Introducing a broker changes the delivery architecture and must be recorded in a
new ADR that supersedes or amends this decision.

## Consequences

The web and worker roles can be deployed, restarted, and scaled independently.
HTTP server process count no longer changes processing concurrency implicitly.
Long-running processing cannot consume the web process event loop or force a web
restart to cancel in-flight work.

The deployment now has two process types. The worker needs its own graceful
shutdown, liveness monitoring, and alerting for queue depth and oldest pending
update age. Without that monitoring, the webhook can continue acknowledging
updates while processing is unavailable.

Both entrypoints need explicit dependency construction and cleanup. Shared
bootstrap helpers may be extracted where they remove actual duplication, but the
application will not introduce a generic process or worker framework solely for
this split.

PostgreSQL remains the only queue infrastructure required by the current MVP.

## Alternatives considered

### Start the worker in the FastAPI lifespan

Rejected as the target architecture because it couples processing concurrency,
restarts, and shutdown to the HTTP server topology. It remains simpler to run but
does not preserve the intended operational boundary between acceptance and
processing.

### Add RabbitMQ now

Rejected because PostgreSQL already provides durable storage, deduplication,
claiming, retry scheduling, and inspectable processing state at the current
scale. RabbitMQ would add another stateful service and a delivery boundary
without removing the need for PostgreSQL processing state.

### Use Celery for the worker lifecycle

Rejected for the current scope. The worker has a small, explicit lifecycle, and
Celery retries do not represent the required distinction between safe retries
and ambiguous non-idempotent mutations.
