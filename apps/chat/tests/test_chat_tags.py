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
    assert result == '<p><a href="http://example.com" target="_blank" rel="noopener noreferrer">Link Text</a></p>'


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
        pytest.param(
            "[Link Text](http://example.com)",
            '<p><a href="http://example.com" target="_blank" rel="noopener noreferrer">Link Text</a></p>',
            id="link",
        ),
        pytest.param(
            "![Image](http://example.com/image.jpg)",
            '<p><img alt="Image" src="http://example.com/image.jpg"></p>',
            id="image",
        ),
        pytest.param(
            "[Link Text](file:example-team:1234:5678)",
            '<p><a href="/a/example-team/experiments/1234/file/5678/" target="_blank" rel="noopener noreferrer">Link Text</a></p>',  # noqa: E501
            id="custom_link",
        ),
        pytest.param(
            "![Image](file:example-team:1234:5678)",
            '<p><img alt="Image" src="/a/example-team/experiments/1234/file/5678/"></p>',
            id="custom_image",
        ),
        pytest.param(
            "[Link Text][0]\n[0]: file:example-team:1234:5678",
            '<p><a href="/a/example-team/experiments/1234/file/5678/" target="_blank" rel="noopener noreferrer">Link Text</a></p>',  # noqa: E501
            id="reference_link",
        ),
        pytest.param(
            "![Image][0]\n[0]: file:example-team:1234:5678",
            '<p><img alt="Image" src="/a/example-team/experiments/1234/file/5678/"></p>',
            id="reference_image",
        ),
        pytest.param(
            "[0]\n[0]: file:example-team:1234:5678",
            '<p><a href="/a/example-team/experiments/1234/file/5678/" target="_blank" rel="noopener noreferrer">0</a></p>',  # noqa: E501
            id="short_reference_link",
        ),
        pytest.param(
            "![0]\n[0]: file:example-team:1234:5678",
            '<p><img alt="0" src="/a/example-team/experiments/1234/file/5678/"></p>',
            id="short_reference_image",
        ),
    ],
)
def test_render_links(markdown_text, expected_result):
    result = render_markdown(markdown_text)
    assert result == expected_result


def test_footnote():
    result = render_markdown("Footnotes[^1]\n[^1]: [file name](file:example-team:1234:5678)")
    assert (
        '<a href="/a/example-team/experiments/1234/file/5678/" target="_blank" rel="noopener noreferrer">file name</a>'
    ) in result


def test_render_markdown_sanitizes_unsafe_html():
    markdown_text = 'This is a test <script>alert("XSS")</script><iframe src="http://malicious.com"></iframe>'
    result = render_markdown(markdown_text)
    sanitized_result = result

    assert "<script>" not in sanitized_result
    assert "<iframe>" not in sanitized_result
    assert "alert(" not in sanitized_result
    assert "http://malicious.com" not in sanitized_result
    assert "This is a test" in sanitized_result
