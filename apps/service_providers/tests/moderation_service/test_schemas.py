import pytest
from pydantic import ValidationError

from apps.service_providers.moderation_service.schemas import ModerationOutput


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            {
                "illicit": {"flagged": True, "score": 0.91},
                "illicit/violent": {"flagged": True, "score": 0.84},
                "hate": {"flagged": False, "score": 0.02},
            },
            {"illicit", "illicit/violent"},
            id="some_flagged",
        ),
        pytest.param({"hate": {"flagged": False, "score": 0.02}}, set(), id="none_flagged"),
    ],
)
def test_flagged_categories(value, expected):
    assert ModerationOutput.model_validate(value).flagged_categories() == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({"hate": {"score": 0.5}}, id="missing_flagged"),
        pytest.param({"hate": {"flagged": True, "score": "high"}}, id="non_numeric_score"),
        pytest.param({"hate": {"flagged": True}}, id="missing_score"),
        pytest.param({"hate": {"flagged": True, "score": float("nan")}}, id="nan_score"),
        pytest.param({"hate": {"flagged": True, "score": 1.5}}, id="score_above_one"),
        pytest.param({"hate": {"flagged": True, "score": -0.1}}, id="negative_score"),
    ],
)
def test_invalid_output(value):
    with pytest.raises(ValidationError):
        ModerationOutput.model_validate(value)
