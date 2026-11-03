"""Rendering of the `templates/cotton/ui/` components."""

import pytest
from django.conf import settings
from django.template import engines
from django.template.loader import render_to_string
from django.test import override_settings
from django.utils.safestring import mark_safe
from django_cotton.compiler_regex import CottonCompiler

from apps.generics.components import render_component
from apps.generics.tables import ColumnWithHelp


def render(source, context=None):
    """Render a template string the way the cotton loader serves a file: `<c-...>` tags compiled first.

    Whitespace is collapsed so assertions can spell out markup without copying template indentation.
    """
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
    ("chip", "expected"),
    [
        pytest.param({"label": "Bot", "url": "/b/"}, '<a href="/b/"', id="chip-with-url"),
        pytest.param({"label": "Bot", "url": ""}, "no-animation", id="chip-without-url"),
        pytest.param("plain text", "plain text", id="plain-value-printed-as-is"),
    ],
)
def test_chip_from_chip_dict(chip, expected):
    assert expected in render('<c-ui.chip :chip="chip" />', {"chip": chip})


@pytest.mark.parametrize(
    ("source", "expected", "unexpected"),
    [
        pytest.param(
            '<c-ui.copy-chip target="link" value="https://x" text="Copy link" extra_classes="btn-ghost" />',
            [
                '<span id="link">https://x</span>',
                "fa-copy",
                "Copy link",
                "btn btn-sm btn-ghost",
                "copyToClipboard(this, 'link')",
            ],
            ["<input"],
            id="shown-value",
        ),
        pytest.param(
            '<c-ui.copy-chip target="secret" :value="value" :show_value="False" />',
            ['<input id="secret" type="hidden" value="s3cret"/>'],
            ['<span id="secret">'],
            id="hidden-value",
        ),
        pytest.param(
            '<c-ui.copy-chip target="elsewhere" />',
            ["copyToClipboard(this, 'elsewhere')"],
            ['id="elsewhere"'],
            id="value-elsewhere",
        ),
    ],
)
def test_copy_chip(source, expected, unexpected):
    html = render(source, {"value": "s3cret"})
    for fragment in expected:
        assert fragment in html
    for fragment in unexpected:
        assert fragment not in html


def test_session_id_chip_nests_a_copy_chip():
    html = render('<c-ui.session-id-chip :external_id="external_id" id_prefix="row" />', {"external_id": "abc"})
    assert '<code class="truncate max-w-32 inline-block" title="abc">abc</code>' in html
    assert '<input id="row-abc" type="hidden" value="abc"/>' in html


def test_modal():
    html = render('<c-ui.modal id="m" title="Title" x-data="{}">Body <b>text</b></c-ui.modal>')
    assert html.startswith('<dialog class="modal" id="m" x-data="{}">')
    assert '<h3 class="text-lg font-bold">Title</h3>' in html
    assert '<div class="py-4">Body <b>text</b></div>' in html


def test_participant_data_diff_changes():
    changes = [
        {"type": "add", "path": "name", "value": "Alex"},
        {"type": "change", "path": "plan", "old": "free", "new": "pro"},
        {"type": "remove", "path": "legacy", "value": "42"},
    ]
    html = render(
        '<c-app.participant-data-diff-changes :changes="changes" text_size="text-xs" />', {"changes": changes}
    )
    assert html.count("text-xs") == 3
    assert '<span class="text-error line-through">free</span>' in html
    assert '<span class="text-success">pro</span>' in html


def test_render_component_from_python():
    html = render_component("ui.chip", chip={"label": "Bot", "url": "/b/"}, truncate=True)
    assert 'title="Bot"' in html
    assert render_component("ui.help", slot="<b>raw</b>").count("&lt;b&gt;raw&lt;/b&gt;") == 1
    assert "<b>safe</b>" in render_component("ui.help", slot=mark_safe("<b>safe</b>"))


def test_partials_and_components_share_the_loader_chain(tmp_path):
    """`{% partialdef %}` (template_partials) and `<c-...>` (cotton) both hook in through template loaders.

    Renders a file through the loader chain configured in settings, so a wrong loader order fails here.
    """
    (tmp_path / "page.html").write_text(
        '{% partialdef item %}<li><c-ui.chip :label="item" /></li>{% endpartialdef %}'
        "<ul>{% for item in items %}{% partial item %}{% endfor %}</ul>"
    )
    templates = [{**settings.TEMPLATES[0], "DIRS": [*settings.TEMPLATES[0]["DIRS"], tmp_path]}]
    with override_settings(TEMPLATES=templates):
        page = " ".join(render_to_string("page.html", {"items": ["a", "b"]}).split())
        partial = " ".join(render_to_string("page.html#item", {"item": "solo"}).split())
    assert page.count("<li>") == 2
    assert "> a </div>" in page
    assert "> b </div>" in page
    assert partial == '<li> <div class="btn btn-sm btn-soft btn-primary no-animation " > solo </div> </li>'


def test_column_with_help_header_renders_the_help_component():
    column = ColumnWithHelp(help_text="<b>Shown</b> on hover", verbose_name="Score")
    header = column.header()
    assert header.startswith("<span>Score</span>")
    assert "<p><b>Shown</b> on hover</p>" in header
    assert ColumnWithHelp(verbose_name="Plain").header() == "Plain"
