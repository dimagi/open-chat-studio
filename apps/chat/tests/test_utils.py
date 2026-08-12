import pytest

from apps.chat.models import Chat
from apps.chat.utils import safe_link_url
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("https://example.com/embed", "https://example.com/embed", id="https"),
        pytest.param("http://example.com/e?a=1#f", "http://example.com/e?a=1#f", id="http-with-query-and-fragment"),
        pytest.param("HtTpS://example.com/", "HtTpS://example.com/", id="https-mixed-case-scheme"),
        pytest.param("http://example.com:8000/", "http://example.com:8000/", id="http-with-port"),
        pytest.param("javascript:alert(document.domain)", None, id="javascript"),
        pytest.param("JaVaScRiPt:alert(1)", None, id="javascript-mixed-case"),
        pytest.param("java\tscript:alert(1)", None, id="javascript-embedded-tab"),
        pytest.param("java\nscript:alert(1)", None, id="javascript-embedded-newline"),
        pytest.param("java\rscript:alert(1)", None, id="javascript-embedded-carriage-return"),
        pytest.param("java\x00script:alert(1)", None, id="javascript-embedded-nul"),
        pytest.param("\x01javascript:alert(1)", None, id="javascript-leading-control"),
        pytest.param("\ufeffjavascript:alert(1)", None, id="javascript-leading-bom"),
        pytest.param("\u3000javascript:alert(1)", None, id="javascript-leading-ideographic-space"),
        pytest.param("javascript\uff1aalert(1)", None, id="javascript-fullwidth-colon"),
        pytest.param("data:text/html;base64,PHNjcmlwdD4=", None, id="data"),
        pytest.param("vbscript:msgbox(1)", None, id="vbscript"),
        pytest.param("https:javascript:alert(1)", None, id="safe-scheme-without-netloc"),
        pytest.param("//evil.example.com/", None, id="scheme-relative"),
        pytest.param("/some/path", None, id="relative-path"),
        pytest.param("http://[unclosed", None, id="malformed-ipv6-host"),
        pytest.param("", None, id="empty-string"),
        pytest.param(None, None, id="none"),
        pytest.param(1234, None, id="non-string"),
    ],
)
def test_safe_link_url(value, expected):
    assert safe_link_url(value) == expected


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("stored_value", "expected"),
    [
        pytest.param("https://example.com/embed", "https://example.com/embed", id="https"),
        pytest.param("javascript:alert(document.domain)", None, id="javascript"),
        pytest.param(None, None, id="not-set"),
        pytest.param(1234, None, id="non-string"),
    ],
)
def test_chat_embed_source_url(stored_value, expected):
    chat = ExperimentSessionFactory.create().chat
    chat.set_metadata(Chat.MetadataKeys.EMBED_SOURCE, stored_value)
    assert chat.embed_source_url == expected
