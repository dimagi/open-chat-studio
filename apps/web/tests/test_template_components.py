"""Rendering of the `templates/cotton/ui/` components and of the loader chain that serves them."""

import pytest
from django.template import engines
from django_cotton.compiler_regex import CottonCompiler


def render(source, context=None):
    """Render a template string the way the cotton loader serves a file: `<c-...>` tags compiled first."""
    compiled = CottonCompiler().process(source)
    return " ".join(engines["django"].from_string(compiled).render(context or {}).split())


@pytest.mark.parametrize(
    ("source", "context", "expected", "unexpected"),
    [
        pytest.param(
            '<c-ui.chip :url="url" :label="label" />',
            {"url": "/sessions/1/", "label": "Session 1"},
            ['<a href="/sessions/1/"', "btn btn-sm btn-soft btn-primary", "> Session 1 </a>"],
            ["max-w-xs", "no-animation"],
            id="link",
        ),
        pytest.param(
            '<c-ui.chip label="Archived" />',
            {},
            ["<div", "no-animation", "Archived"],
            ["<a "],
            id="static",
        ),
        pytest.param(
            '<c-ui.chip :url="url" :label="label" truncate />',
            {"url": "/x", "label": "A long name"},
            ["max-w-xs", '<span class="min-w-0 truncate" title="A long name">A long name</span>'],
            [],
            id="truncate",
        ),
        pytest.param(
            '<c-ui.chip :url="url" :label="label" hx-get="/next" x-data="{open: false}" ::class="open && \'x\'" />',
            {"url": "/x", "label": "L"},
            ['hx-get="/next"', 'x-data="{open: false}"', ":class=\"open && 'x'\""],
            [],
            id="attrs-pass-through",
        ),
        pytest.param('<c-ui.chip label="" />', {}, [], ["<a ", "<div"], id="nothing-without-url-or-label"),
    ],
)
def test_chip(source, context, expected, unexpected):
    html = render(source, context)
    for fragment in expected:
        assert fragment in html
    for fragment in unexpected:
        assert fragment not in html


def test_help_renders_slot_as_html():
    html = render("<c-ui.help>Shown on <b>hover</b></c-ui.help>")
    assert "dropdown-hover" in html
    assert "<p>Shown on <b>hover</b></p>" in html


@pytest.mark.parametrize(
    ("include", "component"),
    [
        pytest.param(
            '{% include "generic/help.html" with help_content="<i>text</i>" %}',
            "<c-ui.help><i>text</i></c-ui.help>",
            id="help",
        ),
        pytest.param(
            '{% include "generic/chip_button.html" with url="/x" label="L" truncate=True %}',
            '<c-ui.chip url="/x" label="L" truncate />',
            id="chip_button",
        ),
    ],
)
def test_include_wrappers_match_components(include, component):
    assert render(include) == render(component)


def test_partials_and_components_share_the_loader_chain():
    """`{% partialdef %}` (template_partials) and `<c-...>` (cotton) both hook in through template loaders."""
    source = (
        '{% partialdef item %}<li><c-ui.chip :label="item" /></li>{% endpartialdef %}'
        "<ul>{% for item in items %}{% partial item %}{% endfor %}</ul>"
    )
    html = render(source, {"items": ["a", "b"]})
    assert html.count("<li>") == 2
    assert "> a </div>" in html
    assert "> b </div>" in html
