# Abuse detection: technical spec

## Overview

How an incoming message is handled when abuse detection is on:

1. **Blocked check.** If the participant is on the team's denylist, processing stops. Web and API users get a fixed "blocked" message; messaging channels get no reply.
2. **Rules.** After the message is stored, simple rules run on it (for example, a check for HTML or script markup). Rule hits are recorded on the message. They do not give a strike.
3. **Start moderation.** If the team has moderation turned on, a background Celery task sends the message text to the moderation provider (OpenAI).
4. **Bot answers.** The bot generates its response as usual, while moderation runs at the same time.
5. **Wait for moderation.** Before the response is sent, the pipeline waits up to 10 seconds for the moderation result:
   - **No result** (rate limit, error or timeout): the response is sent as normal, the message is marked `skipped`, and the team is notified. A result that arrives after the timeout is discarded.
   - **No strike category flagged**: the message is marked `passed` and the response is sent.
   - **A strike category flagged**: the message is marked `flagged`, the bot's response is replaced with a short "your message was flagged" reply. If the team has auto-block on, the participant gets a strike, and at 3 strikes is added to the denylist.

## Base branch

This work is based on PR #4599 (`bt/refusals-chat-path-2341`), which is not merged yet. That PR handles refused and filtered model turns and records them in `ChatMessageMetadataKeys.MODEL_REFUSED`. This work does not change that behaviour.

Each step in [Implementation order](#implementation-order) ships as its own PR. The tasks are tracked as sub-issues of #4269.

## Constants

Set in code, not configurable:

| Name | Value |
| :---- | :---- |
| `STRIKE_LIMIT` | `3` |
| `MODERATION_WAIT_TIMEOUT` | `10` (seconds) |
| `STRIKE_CATEGORIES` | `{"sexual/minors", "illicit", "illicit/violent", "harassment/threatening", "hate/threatening"}` |

Only the categories in `STRIKE_CATEGORIES` give a strike. The provider's result for every category is recorded on the message, but categories outside `STRIKE_CATEGORIES` do not give a strike or change its status.

## Feature flag

`flag_abuse_detection` is a team-scoped waffle flag (see `docs/developer_guides/code_systems/feature_flags.md`). When it is off:

- every new channel stage returns `should_run() == False`;
- the start-session and bot-initiated message checks are skipped;
- the Moderation section of team settings is hidden.

So with the flag off, moderation does not run, strikes are not counted and blocks are not enforced. `DeniedParticipant` rows and `Participant.strikes` are kept, and apply again when the flag is turned back on.

## Data model

All new columns are nullable or have a DB default, so the migrations are safe while the previous release is still serving.

### `ModerationProvider` (`apps/service_providers/models.py`)

A new kind of service provider. It follows the same pattern as `LlmProvider`, `VoiceProvider`, `MessagingProvider` and `TraceProvider`:

- `BaseTeamModel` and `ProviderMixin`;
- a `ModerationProviderType` `TextChoices` with `form_cls` and a service accessor;
- encrypted `config`;
- an entry in `ServiceProvider` (`apps/service_providers/utils.py`), which gives it the standard list/create/edit/delete views.

Details:

- **Types:** `openai` only. The config form takes an API key, plus an optional base URL and organization (the same as the OpenAI LLM provider).
- **Credential check:** the config form's `clean()` makes one moderation call with the entered credentials and a short timeout. An authentication error (`openai.AuthenticationError`) is a form error. Other errors (rate limit, connection) do not block saving, so a temporary outage does not stop a team from setting up.
- **Deleting a provider** that a team has selected sets `Team.moderation_provider` to null (FK `SET_NULL`). A `pre_delete` signal or the delete view also turns off `Team.moderation_enabled` and sends a notification (US-2).

### `Team` fields (`apps/teams/models.py`)

| Field | Type | Default |
| :---- | :---- | :---- |
| `moderation_enabled` | `BooleanField` | `False` (`db_default=False`) |
| `moderation_provider` | `ForeignKey(ModerationProvider, null=True, on_delete=SET_NULL)` | null |
| `auto_block_enabled` | `BooleanField` | `False` (`db_default=False`) |
| `moderation_deleted_provider_name` | `CharField(null=True, blank=True)` | null. Set when the selected provider is deleted, cleared when a provider is selected again. Shown in the Moderation section banner. |

Form validation: `moderation_enabled` requires `moderation_provider`, and the provider must belong to the team.

### `Participant.strikes` (`apps/experiments/models.py`)

```py
strikes = PositiveIntegerField(default=0, db_default=0)
```

Incremented with `F("strikes") + 1` in an `update()` that returns the new value. This way, two concurrent increments both count. Only incremented while `team.auto_block_enabled` is on.

### `DeniedParticipant` (new, `apps/moderation/models.py`)

The denylist. One row means the participant is blocked for that team.

| Field | Notes |
| :---- | :---- |
| `team` | from `BaseTeamModel` |
| `participant` | FK, `on_delete=CASCADE` |
| `source` | `TextChoices`: `auto`, `manual`, `code_node` |
| `reason` | `TextField(blank=True)` |
| `experiment` | FK to the chatbot the block came from. Null, `SET_NULL`. Set for `auto` and `code_node` only. |
| `chat_message` | FK to the human `ChatMessage` that gave the final strike. Null, `SET_NULL`. Set for `auto` only. |
| `created_by` | FK to user. Null, `SET_NULL`. Set for `manual` only. |
| `created_at` | from `BaseModel` |

- `UniqueConstraint(fields=["team", "participant"])`. Rows are created with `get_or_create`, so if two blocks happen at once, the second does nothing.
- Manual additions and removals are audit-logged (see `docs/agents/django_model_auditing.md`).
- Deleted with the participant (cascade), and by the participant data deletion path.

### `ChatMessage.moderation_status` (`apps/chat/models.py`)

A new column, set on human messages only:

```py
moderation_status = CharField(max_length=16, null=True, blank=True, choices=ModerationStatus.choices)
```

| Value | Meaning |
| :---- | :---- |
| `null` | Moderation was not attempted (flag off, team moderation off, or no text). |
| `passed` | The provider ran and flagged no strike category. |
| `flagged` | The provider flagged one or more strike categories. The response was replaced, and the participant got a strike (unless exempt). |
| `skipped` | Moderation was attempted but gave no result (rate-limited, error or timeout). |

The status only reflects the moderation provider. A rule hit never sets `flagged`. Rule hits are recorded in `ChatMessage.moderation_detail.rules`, and the status stays as the provider set it (or `null` if moderation was not attempted).

**Migration:** the column is nullable, so adding it is safe while the previous release is serving. `ChatMessage` is a large table, so the index on `moderation_status` is added in a separate, non-atomic migration with `AddIndexConcurrently` rather than `db_index=True`. This avoids locking the table during the deploy.

### `ChatMessage.moderation_detail` (`apps/chat/models.py`)

A new column on human messages, validated by a pydantic schema. Every provider type converts its output to the same shape:

```py
moderation_detail = SchemaField(schema=ModerationDetail | None, null=True, blank=True, default=None)
```

`SchemaField` is from `django_pydantic_field`, already used in `apps/admin/models.py`. The column is not part of the chat API's message output. `moderation_status` is included in the session export (see [Export](#export)); `moderation_detail` is not.

It records the provider's output, why the message has its status, and which rules fired. `ModerationCategory` and `ModerationOutput` live in `apps/service_providers/moderation_service/schemas.py`, because they are the provider interface's return type. `ModerationDetail` and `ModerationStatus` live in `apps/chat/`, next to the columns that use them:

```py
class ModerationCategory(BaseModel):
    flagged: bool
    score: float


class ModerationOutput(RootModel[dict[str, ModerationCategory]]):
    """Category name to the provider's result for that category."""

    def flagged_categories(self) -> set[str]:
        return {name for name, category in self.root.items() if category.flagged}


class ModerationDetail(BaseModel):
    output: ModerationOutput | None = None
    skip_reason: Literal["rate_limited", "error", "timeout"] | None = None
    rules: list[str] = []
```

Example for a flagged message where a rule also fired:

```json
{
  "output": {
    "illicit": {"flagged": true, "score": 0.91},
    "illicit/violent": {"flagged": true, "score": 0.84},
    "hate": {"flagged": false, "score": 0.02},
    "...": {"flagged": false, "score": 0.0}
  },
  "rules": ["markup"]
}
```

| Field | Set when | Value |
| :---- | :---- | :---- |
| `output` | the provider returned a result (`passed` or `flagged`) | The provider's flag and score for every category it evaluated, keyed by OpenAI's slash-form category names. For OpenAI, built from `categories` and `category_scores` in `results[0]`. |
| `skip_reason` | `moderation_status` is `skipped` | `"rate_limited"`, `"error"` or `"timeout"` |
| `rules` | a rule fired (see `AbuseRulesStage`), whatever the status | Names of the rules that fired, e.g. `["markup"]` |

The column is `null` when no field would have a value. We set no thresholds of our own: the provider's flag decides. The scores are kept so that thresholds could be applied to past messages later.

**Migration:** nullable, added in the same migration as `moderation_status`.

## Moderation provider interface

New package: `apps/service_providers/moderation_service/`.

```py
class ModerationService(ABC):
    @abstractmethod
    def moderate(self, text: str) -> ModerationOutput: ...


class ModerationRateLimited(Exception): ...
```

`ModerationOutput` (see [`ChatMessage.moderation_detail`](#chatmessagemoderation_detail-appschatmodelspy)) maps each category name to `{"flagged": bool, "score": float}`. Every provider type converts its own response to it, mapping its categories to the OpenAI category names.

- If a provider cannot evaluate a category, it leaves the category out rather than reporting it as not flagged.
- `OpenAIModerationService.moderate`:
  - calls `client.moderations.create(model="omni-moderation-latest", input=text)`;
  - builds a `ModerationOutput` by pairing each category in `results[0].categories` with its score in `results[0].category_scores`, keyed by the API's slash-form names (e.g. `illicit/violent`, not the SDK attribute `illicit_violent`);
  - raises `ModerationRateLimited` on HTTP 429 (`openai.RateLimitError`) and lets other errors propagate.
- We do not use the inline `moderation=` option on `responses.create`, because it only works for bots whose LLM is OpenAI.

### OpenAI rate limits

The moderation endpoint is free. Limits depend on the account's usage tier ([omni-moderation-latest](https://developers.openai.com/api/docs/models/omni-moderation-latest)):

| Tier | Requests / minute | Tokens / minute |
| :---- | :---- | :---- |
| Free | 250 | 10,000 |
| 1 | 500 | 10,000 |
| 2 | 500 | 20,000 |
| 3 | 1,000 | 50,000 |
| 4 | 2,000 | 250,000 |
| 5 | 5,000 | 500,000 |

For comparison, the ten busiest minutes in production (human messages across all teams, all history, grouped by `TruncMinute("created_at")`) were:

`630, 404, 377, 364, 344, 291, 289, 281, 275, 254`

- The busiest minute is above the tier 2 request limit.
- On the free tier, 10,000 tokens per minute at 250 messages per minute allows about 40 tokens per message.

## Celery task: `moderate_message`

Location: `apps/moderation/tasks.py` (see [Placement](#placement)).

```py
@shared_task(
    queue=Queues.BACKGROUND,
    autoretry_for=(APIConnectionError, InternalServerError),
    max_retries=2,
    retry_backoff=1,
)
def moderate_message(chat_message_id: int, moderation_provider_id: int) -> dict: ...
```

**What it does:** loads the message text and the provider, then calls `provider.get_moderation_service().moderate(text)`.

**What it returns:**

| Outcome | Return value |
| :---- | :---- |
| Success | `{"status": "ok", "output": {...}}` (the `ModerationOutput`, dumped to JSON) |
| Rate-limited (`ModerationRateLimited`) | `{"status": "skipped", "reason": "rate_limited"}`, immediately, with no retry |
| Connection error or HTTP 5xx | Retried at most 2 times with a short backoff, so the total stays inside `MODERATION_WAIT_TIMEOUT`. If the last retry fails: `{"status": "skipped", "reason": "error"}` |

**It does not write to the database.** All writes happen in `ModerationResultStage`. This means each message has one writer, and a result that arrives after the wait timeout is simply ignored.

**Result storage:** the result goes to the Celery result backend (`CELERY_RESULT_BACKEND`, Redis), so the task does not set `ignore_result=True`.

**Queue: `Queues.BACKGROUND`.**

- The chat worker that runs the channel pipeline blocks while it waits for this task. Running the task on the background worker means it does not need a second chat worker. Otherwise, under load, chat workers waiting on moderation could use up the workers needed to run it.
- Trade-off: the background queue also runs long tasks (indexing, exports, imports). When it is backed up, the task may not start within `MODERATION_WAIT_TIMEOUT`, and the message goes out unmoderated (`skipped`, reason `timeout`).
- A dedicated queue is planned as future work.

## Channel pipeline (`apps/channels/`)

Stage lists are built in four places. The first three get the new stages:

- `apps/channels/channel_base.py`
- `apps/channels/web_channel.py`
- `apps/channels/api_channel.py`

`apps/channels/evaluation_channel.py` does not. Evaluation runs all use one internal "evaluations" participant, which must not collect strikes or be blocked.

New field on `MessageProcessingContext` (`apps/channels/pipeline.py`):

```py
moderation_task_id: str | None = None
```

Stage order (new stages marked):

```text
core stages:
    ...
    ParticipantResolverStage
    BlockedParticipantStage        <- new
    ...
    ChatMessageCreationStage
    AbuseRulesStage                <- new
    ...
    BotInteractionStage
    ResponseFormattingStage

terminal stages:
    ModerationResultStage          <- new, first terminal stage
    ResponseSendingStage
    SendingErrorHandlerStage
    PersistenceStage
    ActivityTrackingStage
```

### `BlockedParticipantStage` (US-14)

Runs directly after `ParticipantResolverStage`. It makes one indexed query:

```py
DeniedParticipant.objects.filter(team=..., participant=ctx.participant).exists()
```

If the participant is blocked:

- **Web widget and API:** raise `EarlyExitResponse` with a fixed blocked message. The API returns it as an explicit blocked response.
- **Messaging channels:** raise `EarlyAbort`, so the participant gets no reply.

### `AbuseRulesStage` (US-6, US-15)

Runs after `ChatMessageCreationStage` (so `moderation_detail` can be written to the stored message) and before `BotInteractionStage`.

1. **Run the rules.** Each rule is a function that takes the message text, its attachments and the participant, and returns either `None` or the rule's name.
   - `markup`: regex for `<script`, `<iframe`, `on\w+=`, `javascript:`, `data:text/html`, `<svg`, `<img … src=`. Recorded only, no strike.
   - `blocked_attachment`: see [Attachment rejection](#attachment-rejection).
2. **Record rule hits.** Write the names of the rules that fired to `moderation_detail.rules` on the human message.
3. **Start moderation**, if all of these are true:
   - `flag_abuse_detection` is active for the team;
   - `team.moderation_enabled` is on and `team.moderation_provider_id` is set;
   - `ctx.user_query` is not empty. This is the only text sent to the provider (for a voice note, it is the transcript).

   Then: `ctx.moderation_task_id = moderate_message.delay(...).id`.

Messages from team members and preview sessions are moderated too.

### `ModerationResultStage` (US-4, US-5, US-11, US-12, US-13)

The first terminal stage. It is a terminal stage, not a core stage, so that it still runs when a core stage exits early (for example, a refused model turn or a bot error). In that case it still records the result and any strike, but there is no bot response to replace.

1. **No task:** if `ctx.moderation_task_id` is `None`, return.
2. **Wait for the result:**

   ```py
   AsyncResult(task_id).get(timeout=MODERATION_WAIT_TIMEOUT, disable_sync_subtasks=False)
   ```

   - `disable_sync_subtasks=False` is needed because messaging channels run the pipeline inside a Celery task, and by default Celery does not allow a blocking `get()` there.
   - The timeout starts when this stage starts waiting, which is after the bot has answered. So the added latency is at most 10 seconds, and usually zero.
   - If the wait times out, the result is discarded even if the task finishes later. The task does not write to the database, so a late result has no effect.
3. **Skipped** (the task returned `skipped`, the wait timed out, or any error while waiting):
   - set `moderation_status = "skipped"` and `moderation_detail.skip_reason`;
   - send the team notification (throttled, see [Notifications](#notifications));
   - send the response as normal.
4. **Moderation ran:** store the output, and work out which strike categories were flagged:

   ```py
   output = ModerationDetail.model_validate({"output": result["output"]}).output
   strike = output.flagged_categories() & STRIKE_CATEGORIES
   ```

   - Set `moderation_detail.output = output`, whatever the status.
   - If `strike` is empty: set `moderation_status = "passed"`.
   - Otherwise: set `moderation_status = "flagged"`, then continue to step 5.
5. **Strike categories flagged:**
   - [Replace the response](#replacing-the-response).
   - If `team.auto_block_enabled` is on and the participant is not exempt (exempt means a team member, or a preview session), increment `Participant.strikes`.
   - If the new strike count is `>= STRIKE_LIMIT`: create a `DeniedParticipant` (`source=auto`, with `experiment`, `chat_message`, and a reason listing the strike categories), and notify the team.
6. **Save** the human message with `update_fields=["moderation_status", "moderation_detail"]`.

### Replacing the response

When the provider flags a strike category, the response is replaced for every participant, including exempt ones:

1. Generate the replacement message with the `EventBot`. This uses the same mechanism as `_generate_error_message` in `apps/channels/pipeline.py`. The prompt tells the participant, in the conversation's language, that their message was flagged and will not be answered. If the `EventBot` fails, use a fixed fallback message.
2. Replace the `content` of the AI `ChatMessage` the bot already stored (`ctx.bot_response`) with the replacement message, and save it. The message keeps its ID and trace link. The bot's original answer is discarded; it is not kept on the message or in its metadata.
3. Clear `ctx.formatted_message`, `ctx.voice_audio`, `ctx.additional_text_message` and `ctx.files_to_send`.
4. Set `ctx.early_exit_response` to the replacement message, so `ResponseSendingStage` sends it. `PersistenceStage` does not store it again, because it skips early exit responses when `ctx.bot_response` exists.

Anything the bot already did while answering (participant data updates, tool calls) is not undone.

### LLM history exclusion

On later turns, the flagged human message is left out of the history sent to the LLM. #4599 added `ChatMessage.is_excluded_from_history` (`apps/chat/models.py`), which is true for a human message with `MODEL_REFUSED` metadata. It is extended to also be true when `moderation_status` is `flagged`, so the history code needs no change.

- The `EventBot` reply that follows it stays in the history, so the LLM sees that a message was declined.
- The flagged message still appears in the session and the transcript.

## Other enforcement points

- **Start-session endpoints** (widget `POST /api/chat/start/`, and the API session start): the same denylist check as `BlockedParticipantStage`. They return an explicit blocked response (US-14).
- **Bot-initiated messages:** `ExperimentSession.ad_hoc_bot_message` (`apps/experiments/models.py`) returns without sending if the participant is on the denylist. This covers scheduled messages, reminders and timeout events. The sessions stay open (US-14).
- **Code node function `block_participant(reason: str)`:** creates a `DeniedParticipant` for the current participant (`source=code_node`, with `experiment` and `reason`). No strikes are added. It is registered with the other code node helpers in `apps/pipelines/nodes/` (US-16).

## Attachment rejection

The email channel already blocks some attachments. That logic is shared with every channel (US-15):

- `EMAIL_BLOCKED_EXTENSIONS` and `EMAIL_BLOCKED_CONTENT_TYPES` are already in `apps/service_providers/file_limits.py`. `_is_blocked` and its `_category` helper move from `apps/channels/email_channel.py` to the same module, so every channel can use them.
- A blocked file is removed from the message's attachments before the bot sees it, and the participant is told the file was rejected. No strike.
- The chat API's `validate_file_upload` also applies the extension and content type check. Today it accepts any `text/*` content type.

## Notifications

All notifications go through `create_notification` (`apps/ocs_notifications/utils.py`), with the notification functions in `apps/ocs_notifications/notifications.py`. Each is sent with `permissions=["experiments.change_experiment", "experiments.change_participant"]`, so it reaches every team member who can edit both chatbots and participants.

| Notification | When | Story |
| :---- | :---- | :---- |
| Moderation skipped | Rate-limited, error or timeout. Throttled per team (for example, at most one per hour), because a rate-limited account would otherwise send one per message. | US-5 |
| Participant blocked automatically | A participant reaches the strike limit and auto-block is on. | US-11 |
| Moderation provider deleted | The team's selected provider is deleted and moderation is turned off. | US-2 |

## UI

Views follow `docs/agents/django_view_security.md`, and querysets are team-scoped (`docs/agents/multi_tenancy.md`).

- **Moderation provider** (US-1): the standard service provider list/create/edit/delete views, via the `ServiceProvider` registry.
- **Team settings, new Moderation section** (US-2 to US-4, US-10). Hidden when the flag is off. Contains:
  - a form for `moderation_enabled`, `moderation_provider` and `auto_block_enabled`;
  - counts of passed / flagged / skipped messages for the last 7 days, grouped by `moderation_status`;
  - the denylist table (participant, source, reason, chatbot, created by, created at), with an unblock action.
- **Transcript** (US-6): two separate markers on human messages:
  - "flagged", with the flagged categories, when `moderation_status` is `flagged`;
  - "rule", with the rule names, when `moderation_detail` has `rules`.
- **Session list** (US-7), in `apps/experiments/filters.py`. Two separate filters:
  - `moderation_status` (passed / flagged / skipped). A session matches if any of its messages has that status.
  - "rule hit": a session matches if any of its messages has `moderation_detail.rules`.
- **Participant page** (US-8, US-9): strike count, and block/unblock buttons. Unblock deletes the `DeniedParticipant` row and sets `strikes` to 0.

## Export

The session export (`apps/experiments/export.py`) gets a `moderation_status` column for each message (US-6). It is empty for AI messages and for human messages that were not moderated. `moderation_detail` is not exported.

## Placement

The stages need somewhere to keep their shared logic (strike handling, blocking, rules).

- A new Django app, `apps/moderation`, holds `DeniedParticipant`, the rules, the task and the enforcement helpers.
- The stages stay in `apps/channels/stages/` and call into `apps/moderation`.
- `ModerationProvider` and the `ModerationOutput` schema stay in `apps/service_providers`, `ModerationDetail` stays in `apps/chat`, and `strikes` stays on `Participant`. `apps/chat` and `apps/service_providers` are domain apps and must not import from `apps/moderation`, which sits above them.

Check `docs/architecture/package-map.md` for dependency direction before creating the app.

## Implementation order

One PR per step, each with its tests.

1. Shared attachment rejection across all channels (abuse type 4).
2. `flag_abuse_detection`, `DeniedParticipant`, `BlockedParticipantStage`, the start-session and bot-initiated message checks, and manual block/unblock on the participant page.
3. `ChatMessage.moderation_status` and `moderation_detail` columns (with the concurrent index), then `AbuseRulesStage` with the markup rule writing `moderation_detail.rules`, and the transcript marker for rule hits.
4. `ModerationProvider` with the OpenAI type, and the Moderation section of team settings (enable, provider, auto-block).
5. The `moderate_message` task, `ModerationResultStage`, `moderation_detail.output` and `skip_reason`, replacing the response with an `EventBot` message, LLM history exclusion, and the skipped notification.
6. `Participant.strikes`, automatic blocking and the team notification.
7. Remaining UI: session-list filters, strike count on the participant page, denylist and status counts in team settings, and the `moderation_status` export column.
8. `block_participant` code node function.

## Technical notes

- **Script injection is mainly a risk in staff views and exports.** The widget sanitises with DOMPurify, and the staff transcript uses `nh3` with no `|safe` on raw message content. The remaining weak points are:
  - the widget allows `<img src>`;
  - the content is re-parsed after sanitising;
  - there is no CSP on the staff UI;
  - CSV exports allow formula injection via a leading `=`, `+`, `-` or `@`.

  The markup rule is mainly a signal of intent, not a defence.
- **Earlier attempt.** A `SafetyLayer` feature existed and was removed. Only `ChatMessageType.safety_layer_choices` (`apps/chat/models.py`) and the `SAFETY_LAYER_RESPONSE` tag (`apps/annotations/models.py`) remain.
- **Hook for #3870 (emergency escalation).** `AbuseRulesStage` and `ModerationResultStage` are where escalation will start and wait for moderation. Its self-harm handling will read the same result in `ModerationResultStage`.
- **WAF.** IP-level blocking already exists at the WAF (the `BlockTempIPs` IP set, managed in ocs-deploy; see `apps/web/waf_analysis.py`). Automatic blocks could feed into it later.

## Future technical work

- **Dedicated moderation queue.** Move `moderate_message` off `Queues.BACKGROUND` to its own queue, so long background tasks cannot delay moderation past the wait timeout. This needs a new entry in `settings.CELERY_TASK_QUEUES` and a worker for it in ocs-deploy.
