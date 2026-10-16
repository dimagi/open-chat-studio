from unittest import mock

import openai
import pytest
from openai.types import ModerationCreateResponse

from apps.service_providers.moderation_service import (
    ModerationRateLimited,
    OpenAIModerationService,
)
from apps.service_providers.moderation_service.schemas import ModerationCategory, ModerationOutput
from apps.utils.tests.provider_errors import connection_error, status_error

CATEGORY_NAMES = [
    "harassment",
    "harassment/threatening",
    "hate",
    "hate/threatening",
    "illicit",
    "illicit/violent",
    "self-harm",
    "self-harm/instructions",
    "self-harm/intent",
    "sexual",
    "sexual/minors",
    "violence",
    "violence/graphic",
]


def moderation_response(categories: dict, scores: dict) -> ModerationCreateResponse:
    return ModerationCreateResponse.model_validate(
        {
            "id": "modr-1",
            "model": "omni-moderation-latest",
            "results": [
                {
                    "flagged": any(categories.values()),
                    "categories": categories,
                    "category_scores": scores,
                    "category_applied_input_types": {name: ["text"] for name in CATEGORY_NAMES},
                }
            ],
        }
    )


def make_service() -> OpenAIModerationService:
    return OpenAIModerationService(openai_api_key="sk-test")


@mock.patch("openai.resources.moderations.Moderations.create")
def test_moderate_keys_every_category_by_slash_name(create):
    categories = dict.fromkeys(CATEGORY_NAMES, False) | {"illicit/violent": True}
    scores = {name: 0.01 for name in CATEGORY_NAMES} | {"illicit/violent": 0.84}
    create.return_value = moderation_response(categories, scores)

    output = make_service().moderate("some text")

    expected = {name: ModerationCategory(flagged=categories[name], score=scores[name]) for name in CATEGORY_NAMES}
    assert output == ModerationOutput(expected)
    create.assert_called_once_with(model="omni-moderation-latest", input="some text")


@mock.patch("openai.resources.moderations.Moderations.create")
def test_moderate_leaves_out_a_category_missing_from_the_response(create):
    categories = dict.fromkeys(CATEGORY_NAMES, False)
    del categories["illicit"]
    scores = {name: 0.01 for name in CATEGORY_NAMES}
    create.return_value = moderation_response(categories, scores)

    output = make_service().moderate("some text")

    assert "illicit" not in output.root
    assert set(output.root) == set(CATEGORY_NAMES) - {"illicit"}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        pytest.param(
            status_error(cls=openai.RateLimitError, status=429, message="Rate limited"),
            ModerationRateLimited,
            id="rate_limit",
        ),
        pytest.param(connection_error(openai.APIConnectionError), openai.APIConnectionError, id="connection_error"),
    ],
)
@mock.patch("openai.resources.moderations.Moderations.create")
def test_moderate_errors(create, error, expected):
    create.side_effect = error

    with pytest.raises(expected):
        make_service().moderate("some text")
