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
        # The assistants UI and its download view were removed (#4254), so legacy
        # `assistant_file:` references in historical messages render as plain text.
        pytest.param(
            "[Link Text](assistant_file:example-team:1234:5678)",
            "<p>Link Text</p>",
            id="legacy_assistant_file_link",
        ),
        pytest.param(
            "![Image](assistant_file:example-team:1234:5678)",
            "<p>Image</p>",
            id="legacy_assistant_file_image",
        ),
        pytest.param(
            "[Link Text][0]\n[0]: assistant_file:example-team:1234:5678",
            "<p>Link Text</p>",
            id="legacy_assistant_file_reference_link",
        ),
        pytest.param(
            "[0]\n[0]: assistant_file:example-team:1234:5678",
            "<p>0</p>",
            id="legacy_assistant_file_short_reference_link",
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


@pytest.mark.parametrize(
    ("markdown_text", "expected_result"),
    [
        pytest.param(
            "1. First item\ncontinues here\n2. Second item\ncontinues here\n3. Third",
            "<ol><li>First item<br>continues here</li><li>Second item<br>continues here</li><li>Third</li></ol>",
            id="numbered_items_with_continuation_lines",
        ),
        pytest.param(
            "1. First item\ncontinues here\n\n2. Second item\ncontinues here\n\n3. Third",
            "<ol><li><p>First item<br>continues here</p></li><li><p>Second item<br>continues here</p></li>"
            "<li><p>Third</p></li></ol>",
            id="numbered_items_separated_by_blank_lines",
        ),
        pytest.param(
            "1. **North Charles**\n    - Address: 54 Washburn\n    - Phone: 617\n"
            "2. **Bridge Clinic**\n    - Offers walk-in",
            "<ol><li><strong>North Charles</strong><ul><li>Address: 54 Washburn</li><li>Phone: 617</li></ul></li>"
            "<li><strong>Bridge Clinic</strong><ul><li>Offers walk-in</li></ul></li></ol>",
            id="four_space_nested_bullets",
        ),
        pytest.param(
            "- a\n    - b\n- c",
            "<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>",
            id="four_space_nested_bullet_list",
        ),
        pytest.param(
            "1. a\n    1. b\n    2. c\n2. d",
            "<ol><li>a<ol><li>b</li><li>c</li></ol></li><li>d</li></ol>",
            id="four_space_nested_numbered_list",
        ),
        pytest.param(
            "- a\n\n- b",
            "<ul><li><p>a</p></li><li><p>b</p></li></ul>",
            id="bullets_separated_by_blank_lines",
        ),
        pytest.param(
            "- one\n* two\n+ three",
            "<ul><li>one</li><li>two</li><li>three</li></ul>",
            id="mixed_bullet_markers",
        ),
        pytest.param("Line one\nline two", "<p>Line one<br>line two</p>", id="paragraph_soft_line_break"),
        pytest.param(
            "It will be cold tonight:\n-5 degrees overnight",
            "<p>It will be cold tonight:<br>-5 degrees overnight</p>",
            id="paragraph_line_starting_with_hyphen",
        ),
        pytest.param(
            "> - quoted item", "<blockquote><ul><li>quoted item</li></ul></blockquote>", id="bullet_in_block_quote"
        ),
        pytest.param(
            "| a | b |\n|---|---|\n| 1 | 2 |",
            "<table><thead><tr><th>a</th><th>b</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>",
            id="table",
        ),
        pytest.param(
            "```\n1. one\n  - two\n```",
            "<pre><code>1. one<br>  - two<br></code></pre>",
            id="fenced_code_with_list_lines",
        ),
        pytest.param(
            "text\n\n    - not a list\n    1. code",
            "<p>text</p><pre><code>- not a list<br>1. code<br></code></pre>",
            id="indented_code_with_list_lines",
        ),
        pytest.param(
            "- a\n    - b\n        - c\n- d",
            "<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li><li>d</li></ul>",
            id="four_space_three_level_nesting",
        ),
        pytest.param(
            "1. Run this:\n\n        - name: build\n          run: make\n\n2. Next",
            "<ol><li><p>Run this:</p><pre><code>- name: build<br>  run: make<br></code></pre></li>"
            "<li><p>Next</p></li></ol>",
            id="code_sample_in_numbered_item",
        ),
        pytest.param(
            "1. a\n   ```\n   - x\n   ```\n2. b",
            "<ol><li>a<br>   ```</li><li>x<br>   ```</li><li>b</li></ol>",
            id="indented_fence_in_numbered_item",
        ),
        pytest.param(
            "* * *\n\n  - c\n  - d",
            "<hr><ul><li>c</li><li>d</li></ul>",
            id="thematic_break_before_indented_list",
        ),
        pytest.param(
            "<pre>\n  - x\n    - y\n</pre>",
            "<pre>  - x<br>    - y<br></pre>",
            id="raw_html_pre_block",
        ),
        pytest.param(
            "- parent\n  ```\n  - first\n  ~~~\n  - second\n  ```",
            "<ul><li>parent<br>  ```</li><li>first<br>  ~~~</li><li>second<br>  ```</li></ul>",
            id="mixed_fence_markers_inside_indented_fence",
        ),
        pytest.param(
            "- first\n - second",
            "<ul><li>first</li><li>second</li></ul>",
            id="one_space_indented_sibling_stays_sibling",
        ),
        pytest.param(
            "- a\n - b\n  - c\n   - d",
            "<ul><li>a</li><li>b</li><li>c</li><li>d</li></ul>",
            id="one_space_steps_stay_siblings",
        ),
    ],
)
def test_render_markdown_preserves_existing_rendering(markdown_text, expected_result):
    assert render_markdown(markdown_text) == expected_result


@pytest.mark.parametrize(
    ("markdown_text", "expected_result"),
    [
        pytest.param(
            "Intro:\n\n1. **North Charles**\n   - Address: 54 Washburn\n   - Phone: 617\n2. **Bridge Clinic**\n"
            "   - Offers walk-in\n3. **Metro Boston**\n   - Peer Support\n\nI hope this helps!",
            "<p>Intro:</p><ol><li><strong>North Charles</strong>"
            "<ul><li>Address: 54 Washburn</li><li>Phone: 617</li></ul></li>"
            "<li><strong>Bridge Clinic</strong><ul><li>Offers walk-in</li></ul></li>"
            "<li><strong>Metro Boston</strong><ul><li>Peer Support</li></ul></li></ol>"
            "<p>I hope this helps!</p>",
            id="three_space_bullets_under_numbered_items",
        ),
        pytest.param(
            "1. **North Charles**\n  - Address: 54 Washburn\n  - Phone: 617\n2. **Bridge Clinic**\n  - Offers walk-in",
            "<ol><li><strong>North Charles</strong><ul><li>Address: 54 Washburn</li><li>Phone: 617</li></ul></li>"
            "<li><strong>Bridge Clinic</strong><ul><li>Offers walk-in</li></ul></li></ol>",
            id="two_space_bullets_under_numbered_items",
        ),
        pytest.param(
            "1. **North Charles**\n\n   - Address: 54 Washburn\n   - Phone: 617\n\n"
            "2. **Bridge Clinic**\n\n   - Offers walk-in",
            "<ol><li><p><strong>North Charles</strong></p>"
            "<ul><li>Address: 54 Washburn</li><li>Phone: 617</li></ul></li>"
            "<li><p><strong>Bridge Clinic</strong></p><ul><li>Offers walk-in</li></ul></li></ol>",
            id="three_space_bullets_with_blank_lines",
        ),
        pytest.param(
            "- a\n  - b\n- c",
            "<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>",
            id="two_space_nested_bullet_list",
        ),
        pytest.param(
            "- a\n  - b\n    - c\n- d",
            "<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li><li>d</li></ul>",
            id="three_levels_two_space_nesting",
        ),
        pytest.param(
            "- a\n  1. b\n- c",
            "<ul><li>a<ol><li>b</li></ol></li><li>c</li></ul>",
            id="numbered_list_inside_bullet",
        ),
        pytest.param(
            "- a\n  - b\n# H\n  - c",
            "<ul><li>a<ul><li>b</li></ul></li></ul><h1>H</h1><ul><li>c</li></ul>",
            id="list_after_heading_without_blank_line",
        ),
        pytest.param(
            "- a\n    - b\n  - c",
            "<ul><li>a<ul><li>b</li><li>c</li></ul></li></ul>",
            id="two_space_item_after_four_space_item",
        ),
        pytest.param(
            "```x``` is inline code\n\n1. a\n   - b",
            "<p><code>x</code> is inline code</p><ol><li>a<ul><li>b</li></ul></li></ol>",
            id="inline_triple_backtick_line_before_list",
        ),
    ],
)
def test_render_markdown_nests_lists_indented_less_than_four_spaces(markdown_text, expected_result):
    assert render_markdown(markdown_text) == expected_result
