# ADR 0001: Use PostgreSQL for durable Telegram update processing

Date: 2026-08-23

Status: Accepted

## Context

The Telegram webhook currently returns a successful response before the update
has been processed. Processing is tracked only by an in-memory task. A process
restart after the response can therefore lose the update. Repeated Telegram
delivery can also run the same use case more than once.

The bot will need PostgreSQL for state that does not belong in Singularity,
including linked Telegram accounts, snooze state, and skip history.

There are two different duplication boundaries:

1. A unique Telegram `update_id` prevents the same incoming update from being
   accepted more than once.
2. An idempotent external operation prevents a retry from applying the same
   mutation more than once in Singularity.

PostgreSQL can provide the first guarantee. It cannot atomically commit a local
processing result together with a remote Singularity HTTP operation. A timeout,
connection loss, or process failure can leave the result of a remote mutation
unknown.

## Decision

PostgreSQL will be the durable inbox for Telegram updates.

The webhook processing contract is:

1. Authenticate the request and validate the Telegram update.
2. Insert the update into PostgreSQL under its unique `update_id`.
3. Return a successful response only after the transaction commits.
4. A repeated `update_id` is acknowledged without executing its use case again.
5. A database failure produces a non-successful webhook response.

A bounded worker will claim stored updates and persist their processing state.
External HTTP calls must not hold a database transaction open. The processing
model distinguishes these outcomes:

* `pending`: ready for processing or for a safe retry;
* `processing`: claimed by a worker;
* `succeeded`: the use case completed;
* `failed`: a terminal result is known;
* `uncertain`: an external mutation may have succeeded, but its result cannot be
  established safely.

An operation may be retried automatically only when at least one of these is
true:

* the application knows that the external request was not sent;
* the operation is read-only;
* the external operation has a verified idempotency guarantee.

An ambiguous non-idempotent mutation must move to `uncertain` instead of being
retried blindly. This avoids duplicate task creation at the cost of requiring
reconciliation or a user-visible failure path.

Recovery of an abandoned `processing` claim follows the same rule. The stored
attempt state must distinguish work that is safe to retry from work that may
have issued an external mutation. An expired claim is not automatically returned
to `pending` when the external outcome may be unknown.

Singularity `/v2/batch` exposes a client operation UUID described as an
idempotency mechanism. Using it for single task mutations is not part of this
decision. It may be adopted only after a contract test verifies repeat,
concurrent repeat, changed-payload, response identity, and retention behavior.

Singularity remains the source of truth for tasks. The inbox may store the
Telegram payload and processing outcome, but it must not become a local task
copy.

Development PostgreSQL will run in Docker Compose with its data directory on a
`tmpfs` mount. Development data must not use a bind mount or persistent Docker
volume. Production PostgreSQL must use durable storage.

## Consequences

PostgreSQL provides atomic `update_id` deduplication, durable acknowledgement,
retry scheduling, and inspectable failure state in the same database that the
bot already needs for runtime state.

The application must add migrations, inbox retention, worker lifecycle, and
recovery rules. PostgreSQL alone does not make external mutations exactly once.

## Alternatives considered

### Continue in-memory processing

Rejected because a successful webhook response can be followed by silent update
loss during a restart or integration failure.

### Use RabbitMQ

Rejected for the current scope. RabbitMQ provides durable delivery but still
requires consumer idempotency and a separate deduplication store. PostgreSQL is
needed by the product regardless.

### Retry every failed Singularity request

Rejected because absence of a response does not prove that a mutation failed.
Blind retry can create duplicate tasks.
