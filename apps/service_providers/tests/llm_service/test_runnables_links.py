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


def test_strip_example_com_links_is_linear_time():
    # Regression for ReDoS (py/polynomial-redos): a malicious/degenerate string must not
    # cause polynomial backtracking. This would hang with the old two-quantifier pattern.
    malicious = "[" + "\\](" * 50000
    start = time.perf_counter()
    _strip_example_com_links(malicious)
    assert time.perf_counter() - start < 1.0
