import time

import pytest

from apps.service_providers.llm_service.runnables import _strip_example_com_links


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "[file1.pdf](https://example.com/download/file-1)",
            "*file1.pdf*",
            id="example-com-link-stripped",
        ),
        pytest.param(
            "[docs](https://other.com/page)",
            "[docs](https://other.com/page)",
            id="non-example-link-kept",
        ),
        pytest.param(
            "[a](https://example.com/1) and [b](https://example.com/2)",
            "*a* and *b*",
            id="multiple-links",
        ),
        pytest.param("[1](https://example.com/x)", "[1](https://example.com/x)", id="footnote-ref-skipped"),
    ],
)
def test_strip_example_com_links(text, expected):
    assert _strip_example_com_links(text) == expected


@pytest.mark.parametrize(
    "malicious",
    [
        pytest.param("[\\" * 200_000, id="many-open-brackets"),
        pytest.param("[a](" * 200_000, id="many-unclosed-links"),
        pytest.param("[" * 200_000, id="pure-open-brackets"),
    ],
)
def test_strip_example_com_links_is_linear_time(malicious):
    # Regression for ReDoS (py/polynomial-redos). These inputs are quadratic against a pattern
    # whose classes only exclude the closing delimiter; excluding the opening delimiter too (and
    # using possessive quantifiers) keeps it linear. 200k chars completes in well under a second.
    start = time.perf_counter()
    _strip_example_com_links(malicious)
    assert time.perf_counter() - start < 1.0
