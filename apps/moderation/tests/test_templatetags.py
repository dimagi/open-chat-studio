import pytest

from apps.chat.models import ChatMessage, ModerationStatus
from apps.moderation.templatetags.moderation_tags import is_flagged


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        pytest.param(ModerationStatus.FLAGGED, True, id="flagged"),
        pytest.param(ModerationStatus.PASSED, False, id="passed"),
        pytest.param(None, False, id="unmoderated"),
    ],
)
def test_is_flagged(status, expected):
    assert is_flagged(ChatMessage(moderation_status=status)) is expected
