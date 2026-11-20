from decimal import Decimal

import pytest
from pydantic import BaseModel

from apps.service_providers.llm_service.decision_api import MAX_NESTING, json_safe


class _Attachment(BaseModel):
    file_id: int


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param({"a": float("nan"), "b": float("inf"), "c": 1.5}, {"a": None, "b": None, "c": 1.5}, id="floats"),
        pytest.param([2**60, 7], [str(2**60), 7], id="large-int"),
        pytest.param({1: Decimal("1.10")}, {"1": "1.10"}, id="key-and-decimal"),
        pytest.param({"files": [_Attachment(file_id=3)]}, {"files": [{"file_id": 3}]}, id="pydantic-model"),
        pytest.param(("x", True, None), ["x", True, None], id="tuple"),
    ],
)
def test_json_safe(value, expected):
    assert json_safe(value) == expected


def test_json_safe_flattens_containers_past_the_nesting_limit():
    value = inner = []
    for _ in range(MAX_NESTING + 5):
        inner.append([])
        inner = inner[0]

    result = json_safe(value)

    for _ in range(MAX_NESTING):
        result = result[0]
    assert isinstance(result, str)
