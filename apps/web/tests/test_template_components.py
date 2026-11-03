"""Rendering of the `templates/cotton/ui/` components."""

import pytest
from django import forms
from django.conf import settings
from django.forms.utils import ErrorList
from django.template import engines
from django.template.loader import render_to_string
from django.test import override_settings
from django.utils.safestring import mark_safe
from django_cotton.compiler_regex import CottonCompiler

from apps.generics.components import render_component
from apps.generics.tables import ColumnWithHelp
from apps.web.templatetags.form_tags import render_field


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
            ['hx-get="/next"', 'x-data="{open: false}"', ':class="open &amp;&amp; &#x27;x&#x27;"'],
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
            '<c-ui.copy-chip target="link" value="https://x" text="Copy link" class="btn-ghost" />',
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
    assert partial == '<li> <div class="btn btn-sm btn-soft btn-primary no-animation"> solo </div> </li>'


def test_column_with_help_header_renders_the_help_component():
    column = ColumnWithHelp(help_text="<b>Shown</b> on hover", verbose_name="Score")
    header = column.header()
    assert header.startswith("<span>Score</span>")
    assert "<p><b>Shown</b> on hover</p>" in header
    assert ColumnWithHelp(verbose_name="Plain").header() == "Plain"


@pytest.mark.parametrize(
    ("source", "expected", "unexpected"),
    [
        pytest.param(
            '<c-ui.button variant="primary" type="submit">Save</c-ui.button>',
            ['<button type="submit" class="btn btn-primary">', "Save </button>"],
            ["variant="],
            id="submit-button",
        ),
        pytest.param(
            "<c-ui.button>Plain</c-ui.button>", ['<button type="button" class="btn">'], [], id="default-button"
        ),
        pytest.param(
            '<c-ui.button href="/x" variant="primary" size="sm" icon="fa-solid fa-plus" class="w-full" hx-boost="true">'
            "Add</c-ui.button>",
            [
                '<a href="/x" class="btn btn-primary btn-sm w-full" hx-boost="true">',
                '<i class="fa-solid fa-plus"></i>Add',
            ],
            ["<button", 'href="/x" href'],
            id="link-with-icon-and-merged-class",
        ),
        pytest.param(
            '<c-ui.button variant="ghost" shape="circle" size="sm" icon="fa-solid fa-ellipsis" aria-label="More" />',
            ['class="btn btn-ghost btn-sm btn-circle" aria-label="More"'],
            [],
            id="icon-only",
        ),
        pytest.param(
            '<c-ui.button href="/x" disabled>No</c-ui.button>',
            ['aria-disabled="true" tabindex="-1" class="btn btn-disabled"'],
            ['disabled="True"'],
            id="disabled-link",
        ),
        pytest.param(
            '<c-ui.button variant="error" style="outline" disabled loading>Busy</c-ui.button>',
            [
                '<button type="button" disabled class="btn btn-error btn-outline">',
                '<span class="loading loading-spinner loading-xs"></span>Busy',
            ],
            ["loading="],
            id="disabled-loading-button",
        ),
    ],
)
def test_button(source, expected, unexpected):
    html = render(source)
    for fragment in expected:
        assert fragment in html
    for fragment in unexpected:
        assert fragment not in html


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            '<c-ui.badge variant="success">Active</c-ui.badge>',
            '<span class="badge badge-success">Active</span>',
            id="variant",
        ),
        pytest.param(
            '<c-ui.badge variant="warning" size="sm" style="outline" icon="fa-solid fa-ban" class="mt-1">'
            "Off</c-ui.badge>",
            '<span class="badge badge-warning badge-outline badge-sm mt-1"><i class="fa-solid fa-ban"></i> Off</span>',
            id="all-options",
        ),
        pytest.param("<c-ui.badge>3</c-ui.badge>", '<span class="badge">3</span>', id="default"),
    ],
)
def test_badge(source, expected):
    assert render(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            '<c-ui.alert variant="info">Text</c-ui.alert>',
            '<div role="alert" class="alert alert-info"> Text </div>',
            id="variant",
        ),
        pytest.param(
            '<c-ui.alert variant="warning" style="soft" layout="vertical" icon="fa-solid fa-triangle-exclamation" '
            'class="mb-4" x-data="{}">Msg</c-ui.alert>',
            '<div role="alert" class="alert alert-warning alert-soft alert-vertical mb-4" x-data="{}"> '
            '<i class="fa-solid fa-triangle-exclamation"></i> Msg </div>',
            id="all-options",
        ),
    ],
)
def test_alert(source, expected):
    assert render(source) == expected


def test_with_classes_merges_escapes_and_skips_declared_parameters():
    html = render(
        '<c-ui.badge variant="info" class="mt-1" :title="title" hx-boost>x</c-ui.badge>', {"title": 'A "q" <b>'}
    )
    assert html == '<span class="badge badge-info mt-1" title="A &quot;q&quot; &lt;b&gt;" hx-boost>x</span>'


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        pytest.param(True, '<span class="badge" hx-boost>x</span>', id="true-renders-bare"),
        pytest.param(False, '<span class="badge">x</span>', id="false-omitted"),
        pytest.param(None, '<span class="badge">x</span>', id="none-omitted"),
        pytest.param("yes", '<span class="badge" hx-boost="yes">x</span>', id="string-kept"),
    ],
)
def test_with_classes_boolean_attributes(flag, expected):
    assert render('<c-ui.badge :hx-boost="flag">x</c-ui.badge>', {"flag": flag}) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            '<c-ui.badge title="{{ name }}">x</c-ui.badge>',
            'title="Rock &amp; Roll&#x27;s"',
            id="interpolated-escaped-once",
        ),
        pytest.param(
            '<c-ui.badge :title="name">x</c-ui.badge>', 'title="Rock &amp; Roll&#x27;s"', id="dynamic-escaped-once"
        ),
        pytest.param('<c-ui.badge :title="safe">x</c-ui.badge>', 'title="pre &amp; escaped"', id="safe-value-kept"),
    ],
)
def test_with_classes_escapes_attribute_values_once(source, expected):
    assert expected in render(source, {"name": "Rock & Roll's", "safe": mark_safe("pre &amp; escaped")})


def test_component_attributes_come_before_pass_through_attributes():
    """The component's own attributes are written first, as `{{ attrs }}` last used to do."""
    html = render('<c-ui.copy-chip target="t" value="v" title="mine" />')
    assert html.index('title="Copy to clipboard"') < html.index('title="mine"')


@pytest.mark.parametrize(
    ("source", "context", "expected", "unexpected"),
    [
        pytest.param(
            '<c-ui.dropdown label="Add" icon="fa-solid fa-plus" variant="primary" size="sm" align="end" id="d">'
            '<li><a href="/new">New</a></li></c-ui.dropdown>',
            {},
            [
                '<div class="dropdown dropdown-end" id="d">',
                '<div tabindex="0" role="button" class="btn btn-primary btn-sm">',
                '<i class="fa-solid fa-plus"></i>Add<i class="fa-solid fa-caret-down"></i>',
                '<ul tabindex="0" class="dropdown-content menu bg-base-100 rounded-box z-10 p-2 shadow-sm border '
                'border-base-300 w-52"> <li><a href="/new">New</a></li> </ul>',
            ],
            ["label=", "<button"],
            id="label-trigger",
        ),
        pytest.param(
            '<c-ui.dropdown placement="top" hover width="w-64" menu_class="menu-sm" :caret="False" label="Up">'
            "<li>x</li></c-ui.dropdown>",
            {},
            ['class="dropdown dropdown-top dropdown-hover"', 'class="btn">', "Up </div>", "w-64 menu-sm"],
            ["fa-caret-down"],
            id="placement-hover-no-caret",
        ),
        pytest.param(
            '<c-ui.dropdown><c-slot name="trigger"><div tabindex="0" role="button" class="btn btn-ghost">'
            "<i></i></div></c-slot><li>x</li></c-ui.dropdown>",
            {},
            ['<div class="dropdown"> <div tabindex="0" role="button" class="btn btn-ghost"><i></i></div> <ul'],
            ['class="btn">', "fa-caret-down"],
            id="trigger-slot",
        ),
    ],
)
def test_dropdown(source, context, expected, unexpected):
    html = render(source, context)
    for fragment in expected:
        assert fragment in html
    for fragment in unexpected:
        assert fragment not in html


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            '<c-ui.tooltip tip="Edit">x</c-ui.tooltip>', '<span data-tip="Edit" class="tooltip">x</span>', id="default"
        ),
        pytest.param(
            '<c-ui.tooltip :tip="tip" position="left" class="ml-1" x-show="on">x</c-ui.tooltip>',
            '<span data-tip="A &quot;q&quot; &lt;b&gt;" class="tooltip tooltip-left ml-1" x-show="on">x</span>',
            id="position-and-escaped-tip",
        ),
    ],
)
def test_tooltip(source, expected):
    assert render(source, {"tip": 'A "q" <b>'}) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<c-ui.tabs>x</c-ui.tabs>", '<div role="tablist" class="tabs tabs-border">x</div>', id="default"),
        pytest.param(
            '<c-ui.tabs style="box" size="sm" class="mt-4" x-data="{}">x</c-ui.tabs>',
            '<div role="tablist" class="tabs tabs-box tabs-sm mt-4" x-data="{}">x</div>',
            id="style-size-attrs",
        ),
        pytest.param('<c-ui.tabs style="">x</c-ui.tabs>', '<div role="tablist" class="tabs">x</div>', id="plain"),
    ],
)
def test_tabs(source, expected):
    assert render(source) == expected


@pytest.mark.parametrize(
    ("source", "context", "expected", "unexpected"),
    [
        pytest.param(
            '<c-ui.tab label="Messages" :count="count" active />',
            {"count": 0},
            [
                '<button type="button" role="tab" aria-selected="true" class="tab tab-active">',
                'Messages<span class="text-base-content/50 ml-1">0</span> </button>',
            ],
            ["tab-content", "<label", "<a "],
            id="active-button-with-zero-count",
        ),
        pytest.param(
            "<c-ui.tab label=\"Data\" ::class=\"tab === 'data' && 'tab-active'\" @click=\"tab = 'data'\" />",
            {},
            ['<button type="button" role="tab" class="tab" :class="tab === &#x27;data&#x27; &amp;&amp;', "@click="],
            ["aria-selected", "<span"],
            id="alpine-button",
        ),
        pytest.param(
            '<c-ui.tab href="/overview" label="Overview" active class="px-6" />',
            {},
            ['<a role="tab" href="/overview" aria-current="page" class="tab tab-active px-6"> Overview </a>'],
            ["<button"],
            id="active-link",
        ),
        pytest.param(
            '<c-ui.tab name="g" label="Sessions" :count="count" active panel_class="pt-3" hx-get="/panel" '
            'hx-trigger="change">Panel</c-ui.tab>',
            {"count": 12},
            [
                '<label role="tab" class="tab" hx-get="/panel" hx-trigger="change">',
                '<input type="radio" name="g" class="hidden" checked />',
                'Sessions<span class="text-base-content/50 ml-1">12</span> </label>',
                '<div role="tabpanel" class="tab-content pt-3">Panel</div>',
            ],
            ["tab-active", "aria-selected"],
            id="radio-tab-with-panel",
        ),
        pytest.param(
            '<c-ui.tab name="g" label="Versions"> </c-ui.tab>',
            {},
            ['<input type="radio" name="g" class="hidden" />'],
            ["checked", "tab-content"],
            id="radio-tab-blank-slot-has-no-panel",
        ),
    ],
)
def test_tab(source, context, expected, unexpected):
    html = render(source, context)
    for fragment in expected:
        assert fragment in html
    for fragment in unexpected:
        assert fragment not in html


@pytest.mark.parametrize(
    ("source", "expected", "unexpected"),
    [
        pytest.param(
            '<c-ui.field label="Name" input_id="id_name" help="Shown <b>bold</b>" :errors="errors" x-show="adv" '
            'class="mt-2">CTRL</c-ui.field>',
            [
                '<div class="fieldset w-full mt-2" x-show="adv">',
                '<label class="label font-bold" for="id_name"> <div> Name <small>',
                "<p>Shown <b>bold</b></p>",
                '</label> CTRL <ul class="errorlist"><li>Required.</li></ul> </div>',
            ],
            ["cursor-pointer"],
            id="label-help-control-errors",
        ),
        pytest.param(
            '<c-ui.field label="On" inline>CTRL</c-ui.field>',
            [
                '<div class="fieldset">',
                '<label class="label font-bold cursor-pointer"> <div> On </div> CTRL </label> </div>',
            ],
            ["w-full", "<small>", "for="],
            id="inline-checkbox-layout",
        ),
    ],
)
def test_field(source, expected, unexpected):
    html = render(source, {"errors": ErrorList(["Required."])})
    for fragment in expected:
        assert fragment in html
    for fragment in unexpected:
        assert fragment not in html


class _FieldForm(forms.Form):
    name = forms.CharField(label="Name", help_text="Shown to <b>participants</b>")
    kind = forms.ChoiceField(label="Kind", choices=[("a", "A")])
    tags = forms.MultipleChoiceField(label="Tags", choices=[("a", "A")], widget=forms.CheckboxSelectMultiple)
    enabled = forms.BooleanField(
        label="Enabled", required=False, widget=forms.CheckboxInput(attrs={"control_attrs": {"x-show": "advanced"}})
    )


@pytest.mark.parametrize(
    ("field", "attrs", "expected", "unexpected"),
    [
        pytest.param(
            "name",
            {"xmodel": "name"},
            [
                '<div class="fieldset w-full" x-model="name">',
                '<label class="label font-bold" for="id_name">',
                "<p>Shown to <b>participants</b></p>",
                '<input class="input w-full " type="text" name="name" required',
                '<ul class="errorlist" id="id_name_error"><li>This field is required.</li></ul>',
            ],
            [],
            id="text",
        ),
        pytest.param(
            "kind", {}, ['<label class="label font-bold" for="id_kind">', "<select"], ["<small>"], id="select"
        ),
        pytest.param(
            "tags",
            {},
            ['<div class="fieldset w-full">', '<label class="label font-bold"> <div> Tags </div>', 'type="checkbox"'],
            ['<label class="label font-bold" for='],
            id="checkbox-multiple",
        ),
        pytest.param(
            "enabled",
            {},
            [
                '<div class="fieldset" x-show="advanced">',
                '<label class="label font-bold cursor-pointer"> <div> Enabled </div> <input class="checkbox "',
                "</label> </div>",
            ],
            ["for=", "w-full"],
            id="checkbox-inline-with-control-attrs",
        ),
    ],
)
def test_render_field_uses_the_field_component(field, attrs, expected, unexpected):
    form = _FieldForm(data={})
    form.is_valid()
    html = " ".join(render_field(form[field], **attrs).split())
    for fragment in expected:
        assert fragment in html
    for fragment in unexpected:
        assert fragment not in html
