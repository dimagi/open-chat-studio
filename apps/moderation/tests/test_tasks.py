from unittest import mock

import openai
import pytest
from pydantic import ValidationError

from apps.moderation.tasks import moderate_message
from apps.service_providers.moderation_service import ModerationRateLimited
from apps.service_providers.moderation_service.schemas import ModerationCategory, ModerationOutput
from apps.utils.factories.service_provider_factories import ModerationProviderFactory
from apps.utils.tests.provider_errors import connection_error, status_error

MODERATE = "apps.service_providers.moderation_service.OpenAIModerationService.moderate"


def _invalid_score_error() -> ValidationError:
    try:
        ModerationCategory(flagged=True, score=2)
    except ValidationError as e:
        return e
    raise AssertionError("score=2 was accepted")


def _run(provider_id: int):
    return moderate_message.apply(kwargs={"text": "hello", "moderation_provider_id": provider_id})


@pytest.mark.django_db()
def test_returns_the_output():
    provider = ModerationProviderFactory.create()
    output = ModerationOutput(
        {
            "illicit": ModerationCategory(flagged=True, score=0.9),
            "hate": ModerationCategory(flagged=False, score=0.01),
        }
    )

    with mock.patch(MODERATE, return_value=output) as moderate:
        result = _run(provider.id)

    moderate.assert_called_once_with("hello")
    assert result.get() == {
        "status": "ok",
        "output": {"illicit": {"flagged": True, "score": 0.9}, "hate": {"flagged": False, "score": 0.01}},
    }


@pytest.mark.django_db()
def test_rate_limited_is_skipped_without_retry():
    provider = ModerationProviderFactory.create()

    with mock.patch(MODERATE, side_effect=ModerationRateLimited("429")) as moderate:
        result = _run(provider.id)

    assert result.get() == {"status": "skipped", "reason": "rate_limited"}
    assert moderate.call_count == 1


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "error",
    [
        pytest.param(connection_error(openai.APIConnectionError), id="connection_error"),
        pytest.param(status_error(openai.InternalServerError, 500, "boom"), id="server_error"),
    ],
)
def test_retries_then_raises(error):
    provider = ModerationProviderFactory.create()

    with mock.patch(MODERATE, side_effect=error) as moderate:
        result = _run(provider.id)

    with pytest.raises(openai.OpenAIError):
        result.get()
    assert moderate.call_count == 3


@pytest.mark.django_db()
def test_retry_that_succeeds_returns_the_output():
    provider = ModerationProviderFactory.create()
    output = ModerationOutput({"hate": ModerationCategory(flagged=False, score=0.01)})

    with mock.patch(MODERATE, side_effect=[connection_error(openai.APIConnectionError), output]):
        result = _run(provider.id)

    assert result.get()["status"] == "ok"


@pytest.mark.django_db()
def test_invalid_provider_output_is_an_error():
    provider = ModerationProviderFactory.create()

    with mock.patch(MODERATE, side_effect=_invalid_score_error()) as moderate:
        result = _run(provider.id)

    assert result.get() == {"status": "skipped", "reason": "error"}
    assert moderate.call_count == 1


@pytest.mark.django_db()
def test_deleted_provider_is_an_error():
    provider = ModerationProviderFactory.create()
    provider_id = provider.id
    provider.delete()

    assert _run(provider_id).get() == {"status": "skipped", "reason": "error"}


@pytest.mark.django_db()
def test_writes_nothing_to_the_database(django_assert_num_queries):
    provider = ModerationProviderFactory.create()

    with mock.patch(MODERATE, return_value=ModerationOutput({})), django_assert_num_queries(1):
        _run(provider.id)
