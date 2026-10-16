from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from celery.exceptions import TimeoutError as CeleryTimeoutError
from celery.result import AsyncResult
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from pydantic import ValidationError

from apps.chat.models import ModerationStatus
from apps.chat.schemas import ModerationDetail
from apps.experiments.models import Participant
from apps.moderation.const import MODERATION_WAIT_TIMEOUT, STRIKE_CATEGORIES, STRIKE_LIMIT
from apps.moderation.enforcement import block_participant, is_exempt
from apps.moderation.models import DeniedParticipantSource
from apps.moderation.rules import run_rules
from apps.moderation.tasks import moderate_message
from apps.ocs_notifications.notifications import participant_auto_blocked_notification
from apps.pipelines.models import PipelineChatMessages
from apps.service_providers.moderation_service.schemas import ModerationOutput
from apps.utils.llm_messages import EMPTY_MESSAGE_PLACEHOLDER

if TYPE_CHECKING:
    from collections.abc import Sequence

    from apps.channels.datamodels import Attachment
    from apps.chat.models import ChatMessage
    from apps.chat.schemas import ModerationSkipReason
    from apps.experiments.models import Experiment
    from apps.teams.models import Team

logger = logging.getLogger("ocs.moderation")


def record_rule_hits(
    *, message: ChatMessage, text: str, attachments: Sequence[Attachment], participant: Participant | None
) -> list[str]:
    """Run the abuse rules and save any that fire on the message's `moderation_detail`."""
    rules = run_rules(text=text, attachments=attachments, participant=participant)
    if rules:
        detail = message.moderation_detail or ModerationDetail()
        message.moderation_detail = detail.model_copy(update={"rules": rules})
        message.save(update_fields=["moderation_detail", "updated_at"])
    return rules


def start_moderation(*, team: Team, message: ChatMessage) -> str | None:
    """Queue the moderation task when the team has moderation on and the message has text. Returns the task id.

    Does not check the feature flag. The message must be committed, as the task reads it.
    """
    if not team.moderation_enabled or team.moderation_provider_id is None:
        return None
    text = message.content
    if not text.strip() or text == EMPTY_MESSAGE_PLACEHOLDER:
        return None
    return moderate_message.delay(chat_message_id=message.id, moderation_provider_id=team.moderation_provider_id).id


def get_moderation_result(task_id: str) -> tuple[ModerationOutput | None, ModerationSkipReason | None]:
    """Wait for the moderation task and return the provider output, or the reason there is none."""
    try:
        # Messaging channels run the pipeline inside a Celery task, where a blocking get() is not
        # allowed by default.
        result = AsyncResult(task_id).get(timeout=MODERATION_WAIT_TIMEOUT, disable_sync_subtasks=False, interval=0.1)
    except CeleryTimeoutError:
        logger.warning("No moderation result for task %s within %s seconds", task_id, MODERATION_WAIT_TIMEOUT)
        return None, "timeout"
    except Exception as error:
        # The task's own failure is already reported by Celery.
        logger.warning("Moderation task %s failed: %r", task_id, error)
        return None, "error"
    if result["status"] != "ok":
        return None, result["reason"]
    try:
        return ModerationOutput.model_validate(result["output"]), None
    except ValidationError:
        logger.exception("Moderation task %s returned an invalid output", task_id)
        return None, "error"


def strike_categories(output: ModerationOutput) -> set[str]:
    """The categories the provider flagged that count towards a strike."""
    return output.flagged_categories() & STRIKE_CATEGORIES


def record_moderation_result(
    *,
    message: ChatMessage,
    output: ModerationOutput | None = None,
    skip_reason: ModerationSkipReason | None = None,
) -> ModerationStatus:
    """Save the message's `moderation_status` and `moderation_detail` together, keeping any rule hits.

    Pass `output` when the provider returned a result, or `skip_reason` when it did not.
    """
    if (output is None) == (skip_reason is None):
        raise ValueError("Pass exactly one of output and skip_reason")
    if output is None:
        status = ModerationStatus.SKIPPED
    elif strike_categories(output):
        status = ModerationStatus.FLAGGED
    else:
        status = ModerationStatus.PASSED
    detail = message.moderation_detail or ModerationDetail()
    message.moderation_detail = detail.model_copy(update={"output": output, "skip_reason": skip_reason})
    message.moderation_status = status
    message.save(update_fields=["moderation_status", "moderation_detail", "updated_at"])
    return status


def add_strike(
    *, participant: Participant, experiment: Experiment, chat_message: ChatMessage, output: ModerationOutput
) -> None:
    """Give the participant a strike for the flagged `chat_message`, and block them at the strike limit.

    `output` is the moderation result for the message. Does nothing when the team has auto-block off or the
    participant is exempt.
    """
    team = experiment.team
    if not team.auto_block_enabled or is_exempt(participant=participant, team=team):
        return
    with transaction.atomic():
        strikes = _increment_strikes(participant)
        if strikes < STRIKE_LIMIT:
            return
        reason = ", ".join(sorted(strike_categories(output)))
        denial, created = block_participant(
            participant=participant,
            source=DeniedParticipantSource.AUTO,
            reason=reason,
            experiment=experiment,
            chat_message=chat_message,
        )
    if created:
        transaction.on_commit(
            lambda: participant_auto_blocked_notification(experiment=experiment, denial=denial, reason=reason)
        )


def _increment_strikes(participant: Participant) -> int:
    """Add one strike in the database and return the new count."""
    participants = Participant.objects.filter(id=participant.id)
    # The row stays locked by the update until the transaction ends, so the count read back is this increment's.
    with transaction.atomic():
        participants.update(strikes=F("strikes") + 1, updated_at=timezone.now())
        participant.strikes = participants.values_list("strikes", flat=True).get()
    return participant.strikes


def remove_turn_from_pipeline_histories(*, human_message: ChatMessage, bot_response: ChatMessage | None) -> None:
    """Delete the node and named history rows the pipeline saved while answering `human_message`.

    These are the session's rows created from the human message up to the bot response (or now, when
    there is none).
    """
    rows = PipelineChatMessages.objects.filter(
        chat_history__session__chat_id=human_message.chat_id, created_at__gte=human_message.created_at
    )
    if bot_response is not None:
        rows = rows.filter(created_at__lte=bot_response.created_at)
    rows.delete()
