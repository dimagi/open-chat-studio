import pytest

from apps.moderation.rules import markup, run_rules


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("<script>alert(1)</script>", id="script"),
        pytest.param("<SCRIPT src='x'></SCRIPT>", id="script_uppercase"),
        pytest.param('<iframe src="https://example.com"></iframe>', id="iframe"),
        pytest.param('<div onclick="steal()">hi</div>', id="event_handler"),
        pytest.param("\" onmouseover='alert(1)", id="event_handler_without_tag"),
        pytest.param("<body onload=alert(1)>", id="event_handler_unquoted"),
        pytest.param("<a href='#' onmouseover = 'x'>", id="event_handler_space_before_equals"),
        pytest.param("\" onmouseover='alert(1)'", id="event_handler_after_quote"),
        pytest.param("[click](javascript:alert(1))", id="javascript_url"),
        pytest.param("java\tscript:alert(1)", id="javascript_url_with_tab"),
        pytest.param("javascript :alert(1)", id="javascript_url_space_before_colon"),
        pytest.param("VBScript:msgbox(1)", id="vbscript_url"),
        pytest.param('<object data="x.swf"></object>', id="object"),
        pytest.param('<embed src="x.swf">', id="embed"),
        pytest.param("data:text/html;base64,PHNjcmlwdD4=", id="data_html_url"),
        pytest.param("<svg/onload=alert(1)>", id="svg"),
        pytest.param('<img alt="x" src="https://example.com/a.png">', id="img_src"),
    ],
)
def test_markup_fires(text):
    assert markup(text, [], None) == "markup"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("Hello, how are you?", id="plain_text"),
        pytest.param("Is 3 < 5 and 7 > 2?", id="comparison"),
        pytest.param("I <3 this bot", id="heart"),
        pytest.param("<b>bold</b> and <i>italic</i>", id="harmless_tags"),
        pytest.param("Python=3.13 is the one I use", id="on_inside_word"),
        pytest.param("Write javascript code for me", id="javascript_word"),
        pytest.param("<img> tags show pictures", id="img_without_src"),
        pytest.param("one = 1", id="word_one_assignment"),
        pytest.param("only=yes", id="word_only_assignment"),
        pytest.param("online = true", id="word_online_assignment"),
        pytest.param("set onboarding=done", id="word_onboarding_assignment"),
        pytest.param("on_call=bob", id="snake_case_assignment"),
        pytest.param("one == two", id="equality"),
        pytest.param("Why does onClick={handle} not fire in React?", id="react_question"),
        pytest.param("", id="empty"),
    ],
)
def test_markup_does_not_fire(text):
    assert markup(text, [], None) is None


def test_run_rules_returns_fired_rule_names():
    assert run_rules(text="<script>", attachments=[], participant=None) == ["markup"]
