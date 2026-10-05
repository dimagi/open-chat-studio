# ADR-0069: Status messages are generated when an API session starts

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Chris Smit · Created: 2026-10-05</p>

## Context

While a reply is being generated, the task-poll endpoint
(`GET /api/chat/<session_id>/<task_id>/poll/`) returns a short status message
such as "Thinking...". The messages come from `ProgressMessagesAgent`, which
asks an LLM for a list of 30 per chatbot. The list was generated on the first
poll in each session, inside the HTTP request, whenever the cache key
`progress_messages:<session_id>` was empty.

The chat widget sends one poll at a time and schedules the next only after the
previous response arrives. The first poll therefore waited on the LLM call, and
every later poll waited behind it. In production this delayed the bot's reply
by tens of seconds, even when the reply was ready sooner.

## Decision

We will generate status messages in a background task when an API session
starts, and the poll request will only read them from the cache.

- `chat_start_session` queues `generate_progress_messages_task` after the
  session is committed. The task caches the agent's output for 24 hours and
  caches nothing if the agent fails.
- The poll request never calls the LLM. On a cache miss it returns
  `message: null`.
- The poll request cycles through the cached list, returning to the first
  message after the last. A session makes at most one LLM call for status
  messages.

## Consequences

- A poll that arrives before the task finishes returns no status message; the
  widget already handles `message: null`.
- Sessions started outside the API get no status messages. The task-poll
  endpoint is only used by API sessions.
- Every API session start costs one LLM call, including sessions that never
  send a message.
- If generation fails, the session shows no status messages; nothing retries.
- Long waits repeat messages instead of showing new ones.

## Alternatives considered

- **Generate on the first poll (previous behaviour)** — rejected; it puts an
  LLM call on the request path that the widget's sequential polling turns into
  a reply delay.
- **Queue the task again when the list runs out** — rejected; polls would get
  no status message until it finished, and repeated polls would need a guard
  against queuing duplicate tasks.
