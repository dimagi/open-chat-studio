from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from celery.exceptions import TimeoutError as CeleryTimeoutError
from celery.result import AsyncResult
from pydantic import ValidationError

from apps.chat.models import ModerationStatus
from apps.chat.schemas import ModerationDetail
from apps.moderation.const import MODERATION_WAIT_TIMEOUT, STRIKE_CATEGORIES
from apps.moderation.rules import run_rules
from apps.moderation.tasks import moderate_message
from apps.service_providers.moderation_service.schemas import ModerationOutput
from apps.utils.llm_messages import EMPTY_MESSAGE_PLACEHOLDER

if TYPE_CHECKING:
    from collections.abc import Sequence

    from apps.channels.datamodels import Attachment
    from apps.chat.models import ChatMessage
    from apps.chat.schemas import ModerationSkipReason
    from apps.experiments.models import Participant
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


def start_moderation(*, team: Team, text: str | None) -> str | None:
    """Queue the moderation task when the team has moderation on and there is text. Returns the task id.

    Does not check the feature flag.
    """
    if not team.moderation_enabled or team.moderation_provider_id is None:
        return None
    if not text or not text.strip() or text == EMPTY_MESSAGE_PLACEHOLDER:
        return None
    return moderate_message.delay(text=text, moderation_provider_id=team.moderation_provider_id).id


def get_moderation_result(task_id: str) -> tuple[ModerationOutput | None, ModerationSkipReason | None]:
    """Wait for the moderation task and return the provider output, or the reason there is none."""
    try:
        # Messaging channels run the pipeline inside a Celery task, where a blocking get() is not
        # allowed by default.
        result = AsyncResult(task_id).get(timeout=MODERATION_WAIT_TIMEOUT, disable_sync_subtasks=False, interval=0.1)
    except CeleryTimeoutError:
        logger.warning("No moderation result for task %s within %s seconds", task_id, MODERATION_WAIT_TIMEOUT)
        return None, "timeout"
    except Exception:
        logger.exception("Moderation task %s failed", task_id)
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
