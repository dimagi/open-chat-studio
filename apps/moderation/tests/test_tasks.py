from unittest import mock

import openai
import pytest

from apps.chat.models import ChatMessageType
from apps.moderation.tasks import moderate_message
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.moderation_service import ModerationRateLimited
from apps.service_providers.moderation_service.schemas import ModerationCategory, ModerationOutput
from apps.utils.factories.experiment import ChatFactory, ChatMessageFactory
from apps.utils.factories.service_provider_factories import ModerationProviderFactory
from apps.utils.tests.provider_errors import connection_error, status_error

MODERATE = "apps.service_providers.moderation_service.OpenAIModerationService.moderate"


def _run(provider_id: int, message_id: int | None = None):
    if message_id is None:
        message_id = ChatMessageFactory.create(
            chat=ChatFactory.create(), message_type=ChatMessageType.HUMAN, content="hello"
        ).id
    return moderate_message.apply(kwargs={"chat_message_id": message_id, "moderation_provider_id": provider_id})


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
@pytest.mark.parametrize(
    ("side_effect", "delete_provider", "expected_reason", "expected_calls"),
    [
        pytest.param(ModerationRateLimited("429"), False, "rate_limited", 1, id="rate_limited"),
        pytest.param(
            lambda text: ModerationCategory(flagged=True, score=2), False, "error", 1, id="invalid_provider_output"
        ),
        pytest.param(
            ServiceProviderConfigError(provider_type="openai", message="bad key"), False, "error", 1, id="config_error"
        ),
        pytest.param(None, True, "error", 0, id="deleted_provider"),
    ],
)
def test_is_skipped_without_retry(side_effect, delete_provider, expected_reason, expected_calls):
    provider = ModerationProviderFactory.create()
    if delete_provider:
        provider.delete()

    with mock.patch(MODERATE, side_effect=side_effect) as moderate:
        result = _run(provider.id)

    assert result.get() == {"status": "skipped", "reason": expected_reason}
    assert moderate.call_count == expected_calls


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
def test_skips_a_deleted_message():
    provider = ModerationProviderFactory.create()

    with mock.patch(MODERATE) as moderate:
        result = _run(provider.id, message_id=0)

    assert result.get() == {"status": "skipped", "reason": "error"}
    moderate.assert_not_called()


@pytest.mark.django_db()
def test_writes_nothing_to_the_database(django_assert_num_queries):
    provider = ModerationProviderFactory.create()
    message = ChatMessageFactory.create(chat=ChatFactory.create(), message_type=ChatMessageType.HUMAN, content="hi")

    with mock.patch(MODERATE, return_value=ModerationOutput({})), django_assert_num_queries(2):
        _run(provider.id, message_id=message.id)
