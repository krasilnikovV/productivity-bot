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

Telegram is the user interface.

Singularity owns task data.

PostgreSQL stores bot-specific state.

Calendar provides time constraints.

The LLM is used for language-related operations.

## Components

### Telegram interface

Handles Telegram webhooks, buttons, messages, and outgoing notifications.

### Application core

Contains the main use cases and coordinates integrations.

Examples:

* capture task;
* get next action;
* complete task;
* skip task;
* snooze task;
* decompose task.

### Decision engine

Selects the next task.

Inputs may include:

* deadline;
* duration;
* priority;
* calendar availability;
* tags and context;
* snooze state;
* recent skips.

The main ranking logic should be deterministic and testable.

### Singularity adapter

Wraps the Singularity API.

Responsible for reading and updating tasks, projects, tags, and related entities.

Application code should not depend directly on Singularity API details.

### Calendar adapter

Provides events and busy intervals.

Calendar data is used as a constraint for task selection.

### LLM adapter

Provides operations such as:

* parse natural-language task input;
* classify a task;
* decompose a task;
* generate clarification questions;
* break ranking ties.

The application should not depend on a specific LLM provider.

### Persistence

PostgreSQL stores state that does not belong in Singularity.

## Data ownership

### Singularity

Stores:

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
```

The schema can change as implementation details become clearer.

### Calendar

Calendar events stay in the calendar provider.

The bot only reads the information required for planning.

## Main flows

### Capture

```text
Telegram message
      ↓
interpret
      ↓
clarify if needed
      ↓
create task in Singularity
      ↓
reply
```

### Next action

```text
request
   ↓
load Singularity tasks
   ↓
load calendar constraints
   ↓
load runtime state
   ↓
filter and rank
   ↓
return one task
```

### Complete

Complete the task in Singularity.

### Skip

Record the rejection and select another candidate.

### Snooze

Store temporary snooze state in PostgreSQL.

Do not change the task deadline just because the user does not want to do it right now.

### Decomposition

Use the LLM to produce a smaller actionable structure and store the result as Singularity subtasks.

## Background jobs

Later versions may run background jobs for:

* morning briefs;
* deadline checks;
* overdue tasks;
* repeatedly skipped tasks;
* task cleanup.

They should avoid notifying the user unless user input is actually needed.

## Constraints

* Singularity is the task source of truth.
* PostgreSQL must not become a duplicate task database.
* Telegram is the primary UI.
* Proactive messages go to the main Telegram account.
* Next-action requests work from every linked account.
* External APIs should be isolated behind adapters.
* LLM output must be validated before it changes external state.
