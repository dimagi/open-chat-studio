from __future__ import annotations

from typing import TYPE_CHECKING

from apps.chat.schemas import ModerationDetail
from apps.moderation.rules import run_rules

if TYPE_CHECKING:
    from collections.abc import Sequence

    from apps.channels.datamodels import Attachment
    from apps.chat.models import ChatMessage
    from apps.experiments.models import Participant


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
