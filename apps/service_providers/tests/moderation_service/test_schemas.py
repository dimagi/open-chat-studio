import pytest
from pydantic import ValidationError

from apps.service_providers.moderation_service.schemas import ModerationOutput


def test_flagged_categories():
    output = ModerationOutput.model_validate(
        {
            "illicit": {"flagged": True, "score": 0.91},
            "illicit/violent": {"flagged": True, "score": 0.84},
            "hate": {"flagged": False, "score": 0.02},
        }
    )
    assert output.flagged_categories() == {"illicit", "illicit/violent"}


def test_flagged_categories_empty():
    output = ModerationOutput.model_validate({"hate": {"flagged": False, "score": 0.02}})
    assert output.flagged_categories() == set()


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({"hate": {"score": 0.5}}, id="missing_flagged"),
        pytest.param({"hate": {"flagged": True, "score": "high"}}, id="non_numeric_score"),
        pytest.param({"hate": {"flagged": True}}, id="missing_score"),
    ],
)
def test_invalid_output(value):
    with pytest.raises(ValidationError):
        ModerationOutput.model_validate(value)
