from django.template.loader import render_to_string


def test_breadcrumbs_template():
    rendered = render_to_string(
        "generic/breadcrumbs.html",
        {"breadcrumbs": [("Chatbots", "/a/team/chatbots/"), ("Cross-team", None), ("<b>Bot</b>", "/ignored/")]},
    )
    assert '<li><a href="/a/team/chatbots/">Chatbots</a></li>' in rendered
    assert "<li>Cross-team</li>" in rendered
    assert '<li class="pg-breadcrumb-active" aria-current="page">&lt;b&gt;Bot&lt;/b&gt;</li>' in rendered
    assert "/ignored/" not in rendered
