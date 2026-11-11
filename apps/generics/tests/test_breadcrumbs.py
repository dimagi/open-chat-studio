from apps.generics.components import render_component


def test_breadcrumbs_component():
    rendered = render_component(
        "ui.breadcrumbs",
        items=[("Chatbots", "/a/team/chatbots/"), ("Cross-team", None), ("<b>Bot</b>", "/ignored/")],
    )
    assert '<li><a href="/a/team/chatbots/">Chatbots</a></li>' in rendered
    assert "<li>Cross-team</li>" in rendered
    assert '<li class="pg-breadcrumb-active" aria-current="page">&lt;b&gt;Bot&lt;/b&gt;</li>' in rendered
    assert "/ignored/" not in rendered
