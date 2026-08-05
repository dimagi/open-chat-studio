import pytest

from apps.utils.llm_messages import EMPTY_MESSAGE_PLACEHOLDER, ensure_non_empty_text


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("", EMPTY_MESSAGE_PLACEHOLDER, id="empty"),
        pytest.param(" ", EMPTY_MESSAGE_PLACEHOLDER, id="space"),
        pytest.param("\n\t\r ", EMPTY_MESSAGE_PLACEHOLDER, id="whitespace-only"),
        pytest.param("Hi", "Hi", id="text"),
        pytest.param("  Hi  ", "  Hi  ", id="surrounding-whitespace-preserved"),
        pytest.param("0", "0", id="falsy-looking-text"),
    ],
)
def test_ensure_non_empty_text(content, expected):
    assert ensure_non_empty_text(content) == expected
