# ADR-0069: Status messages are generated in the background

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Chris Smit · Created: 2026-10-05</p>

## Context

While a reply is being generated, the task-poll endpoint
(`GET /api/chat/<session_id>/<task_id>/poll/`) returns a short status message
such as "Thinking...". The messages come from `ProgressMessagesAgent`, which
asks an LLM for a list of 30 per chatbot. The list was generated inside the
poll request whenever the cache key `progress_messages:<session_id>` was empty.

The chat widget sends one poll at a time and schedules the next only after the
previous response arrives. A poll that missed the cache therefore waited on the
LLM call, and every later poll waited behind it. In production this delayed
the bot's reply by tens of seconds, even when the reply was ready sooner.

## Decision

We will generate status messages in a Celery task, and the poll request will
only read them from the cache.

- On a cache miss, the poll queues `generate_progress_messages_task` and
  returns `message: null`. A cache lock (`progress_generating:<session_id>`,
  60 seconds) allows at most one queued task per session per minute.
- The task caches the agent's output for 24 hours and caches nothing if the
  agent fails. Each read resets the 24-hour expiry, so an active session keeps
  its messages.
- The poll cycles through the cached list, returning to the first message after
  the last.

## Consequences

- The first reply in a session usually shows no status messages, because
  generation starts on its first poll; the widget already handles
  `message: null`.
- A session recovers on its next poll if its messages expire, are evicted, or
  failed to generate.
- If the agent keeps failing, each polling session makes one LLM attempt per
  minute.
- Long waits repeat messages instead of showing new ones.

## Alternatives considered

- **Generate inside the poll request (previous behaviour)** — rejected; it puts
  an LLM call on the request path that the widget's sequential polling turns
  into a reply delay.
- **Generate when an API session starts** — rejected; it does not help the
  first reply, since the widget starts the session as it sends the first
  message, and the poll still needs to regenerate after expiry or eviction.
- **Generate a new list when the list runs out** — rejected; polls would get no
  status message while it ran, and looping over the kept list costs nothing.
