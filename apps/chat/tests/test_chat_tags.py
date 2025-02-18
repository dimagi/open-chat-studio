import pytest
from unittest.mock import patch
from apps.chat.templatetags.chat_tags import render_markdown

@pytest.fixture
def mock_reverse():
    with patch("django.urls.reverse") as mock_reverse:
        yield mock_reverse

def test_render_markdown(mock_reverse):
    mock_reverse.return_value = "/mocked/url"

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
def test_render_markdown_empty(mock_reverse):
    markdown_text = ""
    result = render_markdown(markdown_text)
    assert result == ""

# Test with markdown that contains no links or images
def test_render_markdown_no_links_or_images(mock_reverse):
    markdown_text = "This is a simple text with no links or images."
    result = render_markdown(markdown_text)
    assert result == "<p>This is a simple text with no links or images.</p>"


# Test with markdown containing HTML-like tags
def test_render_markdown_html_tags(mock_reverse):
    markdown_text = "<div>Some HTML content</div>"
    result = render_markdown(markdown_text)
    assert result == "<div>Some HTML content</div>"


# Test markdown with special characters
def test_render_markdown_special_characters(mock_reverse):
    markdown_text = "Special characters: <, >, &"
    result = render_markdown(markdown_text)
    print(result)
    assert result == "<p>Special characters: &lt;, &gt;, &amp;</p>"

def test_render_image_markdown(mock_reverse):
    mock_reverse.return_value = "/mocked/url"
    markdown_text = "![Image](file:example-team:1234:5678)"
    result = render_markdown(markdown_text)
    assert result == '<p><img alt="Image" src="/a/example-team/experiments/1234/file/5678/" /></p>'


