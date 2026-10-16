import pytest

from apps.chat.models import ChatMessageType
from apps.chat.schemas import ModerationDetail
from apps.moderation.services import record_rule_hits
from apps.service_providers.moderation_service.schemas import ModerationCategory, ModerationOutput
from apps.utils.factories.experiment import ChatFactory, ChatMessageFactory

OUTPUT = ModerationOutput({"harassment": ModerationCategory(flagged=False, score=0.1)})


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("text", "initial", "expected"),
    [
        pytest.param("hello", None, None, id="no_hit"),
        pytest.param("<script>", None, ModerationDetail(rules=["markup"]), id="hit"),
        pytest.param(
            "<script>",
            ModerationDetail(output=OUTPUT),
            ModerationDetail(output=OUTPUT, rules=["markup"]),
            id="hit_keeps_existing_detail",
        ),
    ],
)
def test_record_rule_hits(text, initial, expected):
    message = ChatMessageFactory.create(
        chat=ChatFactory.create(), message_type=ChatMessageType.HUMAN, content=text, moderation_detail=initial
    )

    record_rule_hits(message=message, text=text, attachments=[], participant=None)

    message.refresh_from_db()
    assert message.moderation_detail == expected


@pytest.mark.django_db()
def test_record_rule_hits_updates_updated_at():
    message = ChatMessageFactory.create(
        chat=ChatFactory.create(), message_type=ChatMessageType.HUMAN, content="<script>"
    )
    before = message.updated_at

    record_rule_hits(message=message, text="<script>", attachments=[], participant=None)

    message.refresh_from_db()
    assert message.updated_at > before
