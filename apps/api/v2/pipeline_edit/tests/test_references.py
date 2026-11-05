import pytest

from apps.api.v2.pipeline_edit.references import _is_not_allowed


@pytest.mark.parametrize(
    ("item", "allowed", "expected"),
    [
        pytest.param(5, {5}, False, id="int-id"),
        pytest.param("5", {5}, False, id="numeric-string-id"),
        pytest.param(6, {5}, True, id="unreachable-int"),
        pytest.param("6", {5}, True, id="unreachable-string"),
        pytest.param("abc", {5}, True, id="non-numeric-string"),
        pytest.param(True, {1}, True, id="bool-is-not-an-id"),
        pytest.param(1.0, {1}, True, id="float-is-not-an-id"),
        pytest.param([1], {1}, True, id="unhashable"),
        pytest.param("web_search", {"web_search"}, False, id="tool-name"),
        pytest.param("other", {"web_search"}, True, id="unknown-tool-name"),
    ],
)
def test_is_not_allowed(item, allowed, expected):
    assert _is_not_allowed(item, allowed) is expected
