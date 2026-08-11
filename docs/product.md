# Product

## Goal

The bot should let the user manage tasks mostly through Telegram without having to maintain Singularity manually.

The user writes things naturally:

> Pay for the internet before Friday.

The backend decides how this maps to Singularity fields such as project, deadline, duration, tags, or subtasks.

Singularity remains available as a normal task manager, but opening it should not be required for everyday use.

## How it should work

Telegram is the main entry point.

The user can:

* add tasks with normal messages;
* ask what to do next;
* mark a task as done;
* skip or postpone it;
* say that a task is too difficult to start.

The system should return one next action instead of a list of possible tasks.

Routine maintenance should be automated where possible. This includes things like unfinished tasks, overdue tasks, missing metadata, and repeatedly skipped tasks.

The system should ask questions only when missing information affects the actual action. It should not ask the user to manually choose a project, tag, duration, or priority unless necessary.

## Singularity

Singularity is the source of truth for task data.

The bot should use native Singularity entities instead of recreating them:

* tasks;
* projects and subprojects;
* subtasks;
* tags;
* dates and deadlines;
* duration;
* priority;
* recurring tasks.

The bot may assign or update these fields automatically.

## Telegram accounts

One user may have several linked Telegram accounts.

One account is marked as the main account and receives proactive messages such as the morning brief.

`What should I do next?` should work from any linked account.

## Main flows

### Capture

User sends:

> Call the doctor tomorrow.

The bot interprets the message, creates a task in Singularity, and replies with a short confirmation.

### Next action

User presses:

> What should I do next?

The backend checks available tasks, deadlines, duration, calendar events, and temporary task state.

It returns one action.

### Complete

The corresponding Singularity task is completed.

### Skip

The current task is skipped and another candidate is selected.

Repeated skips may later be used as a signal that something is wrong with the task.

### Snooze

The task is temporarily excluded from next-action selection.

### Too difficult

If the task is too vague or difficult to start, the bot should help turn it into a smaller concrete action.

The resulting subtasks should be stored in Singularity.

### Morning brief

The main Telegram account receives a short summary of the day.

It should contain important calendar events, deadlines, and constraints, not a long todo list.

## LLM usage

The LLM is useful for:

* interpreting natural-language input;
* classification;
* decomposition;
* asking clarification questions;
* choosing between otherwise similar task candidates.

Basic scheduling and ranking rules should stay deterministic where possible.

## MVP

The first version should support:

1. adding a task from Telegram;
2. storing it in Singularity;
3. getting one next action;
4. completing, skipping, snoozing, or decomposing it;
5. using calendar events when choosing the next action.

Morning briefs and automatic maintenance can be added after the main loop works well.

## Out of scope

Not required for the first version:

* automatic full-day scheduling;
* complex time blocking;
* voice input;
* habit tracking;
* productivity analytics;
* gamification;
* long-term goal planning;
* general-purpose assistant features.
