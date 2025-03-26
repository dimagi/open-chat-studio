import pytest

from apps.generics.help import replace_markdown_links_with_its_name


@pytest.mark.parametrize(
    ("text", "expected_text"),
    [
        ("testing [this](file:team_slug:1:2) and ![that](file:another_team:32:12)", "testing this and that"),
        ("testing this and that", "testing this and that"),
        ("testing [this](https://example.com)", "testing [this](https://example.com)"),
    ],
)
def test_replace_markdown_links_with_its_name(text, expected_text):
    assert replace_markdown_links_with_its_name(text) == expected_text
