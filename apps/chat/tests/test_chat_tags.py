import pytest

from apps.chat.templatetags.chat_tags import render_markdown


def test_render_markdown():
    # Test markdown with bold text
    markdown_text = "**Bold Text**\n\nSome text"
    result = render_markdown(markdown_text)
    assert result == "<p><strong>Bold Text</strong></p><br><p>Some text</p>"

    # Test markdown with italic text
    markdown_text = "*Italic Text*"
    result = render_markdown(markdown_text)
    assert result == "<p><em>Italic Text</em></p>"

    # Test markdown with custom file link
    markdown_text = "[Link Text](http://example.com)"
    result = render_markdown(markdown_text)
    assert result == '<p><a href="http://example.com" target="_blank">Link Text</a></p>'


# Test with empty markdown text
def test_render_markdown_empty():
    markdown_text = ""
    result = render_markdown(markdown_text)
    assert result == ""


# Test with markdown that contains no links or images
def test_render_markdown_no_links_or_images():
    markdown_text = "This is a simple text with no links or images."
    result = render_markdown(markdown_text)
    assert result == "<p>This is a simple text with no links or images.</p>"


# Test with markdown containing HTML-like tags
def test_render_markdown_html_tags():
    markdown_text = "<div>Some HTML content</div>"
    result = render_markdown(markdown_text)
    assert result == "<div>Some HTML content</div>"


# Test markdown with special characters
def test_render_markdown_special_characters():
    markdown_text = "Special characters: <, >, &"
    result = render_markdown(markdown_text)
    assert result == "<p>Special characters: &lt;, &gt;, &amp;</p>"


@pytest.mark.parametrize(
    ("markdown_text", "expected_result"),
    [
        ("[Link Text](http://example.com)", '<p><a href="http://example.com" target="_blank">Link Text</a></p>'),
        ("![Image](http://example.com/image.jpg)", '<p><img alt="Image" src="http://example.com/image.jpg" /></p>'),
        (
            "[Link Text](file:example-team:1234:5678)",
            '<p><a href="/a/example-team/experiments/1234/file/5678/" target="_blank">Link Text</a></p>',
        ),
        (
            "![Image](file:example-team:1234:5678)",
            '<p><img alt="Image" src="/a/example-team/experiments/1234/file/5678/" /></p>',
        ),
        (
            "[Link Text][0]\n[0]: file:example-team:1234:5678",
            '<p><a href="/a/example-team/experiments/1234/file/5678/" target="_blank"/>Link Text</a></p>',
        ),
        (
            "![Image][0]\n[0]: file:example-team:1234:5678",
            '<p><img alt="Image" src="/a/example-team/experiments/1234/file/5678/" /></p>',
        ),
    ],
)
def test_render_links(markdown_text, expected_result):
    result = render_markdown(markdown_text)
    assert result == expected_result
