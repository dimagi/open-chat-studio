from unittest import mock

import pytest
from django.core.cache import caches

from apps.chat.agent.openapi_tool import openapi_spec_op_to_function_def
from apps.chat.tests.openapi_helpers import make_openapi_schema
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.service_provider_factories import AuthProviderFactory
from apps.utils.openapi import OpenAPISpec

ONE_PER_WINDOW = {"custom_actions": {"rate": "1/5m", "fail_open": True}}
RATE_LIMITED = "Rate limit exceeded"


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    caches["rate_limit"].clear()


def _call(custom_action):
    spec = OpenAPISpec.from_spec_dict(make_openapi_schema({}))
    function_def = openapi_spec_op_to_function_def(spec, "/test", "get")
    tool = function_def.build_tool(custom_action.get_auth_service(), custom_action)
    return str(tool.run({}, tool_call_id="123").content)


def _bearer_provider(team):
    return AuthProviderFactory.create(team=team, type="bearer", config={"token": "secret"})


def _action_pair(team, pairing):
    match pairing:
        case "same-provider":
            provider = _bearer_provider(team)
            return (
                CustomActionFactory.create(team=team, auth_provider=provider),
                CustomActionFactory.create(team=team, auth_provider=provider),
            )
        case "different-providers":
            return (
                CustomActionFactory.create(team=team, auth_provider=_bearer_provider(team)),
                CustomActionFactory.create(team=team, auth_provider=_bearer_provider(team)),
            )
        case "same-action-without-provider":
            action = CustomActionFactory.create(team=team)
            return action, action
        case "different-actions-without-provider":
            return CustomActionFactory.create(team=team), CustomActionFactory.create(team=team)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("pairing", "second_is_limited"),
    [
        pytest.param("same-provider", True, id="actions-sharing-a-provider-share-a-count"),
        pytest.param("different-providers", False, id="each-provider-has-its-own-count"),
        pytest.param("same-action-without-provider", True, id="action-without-provider-is-counted"),
        pytest.param("different-actions-without-provider", False, id="each-action-without-provider-has-its-own-count"),
    ],
)
def test_requests_are_counted_per_provider_then_per_action(pairing, second_is_limited, team, httpx_mock, settings):
    settings.RATE_LIMITS = ONE_PER_WINDOW
    settings.RATE_LIMIT_ENFORCE = True
    httpx_mock.add_response(url="https://example.com/test", text="ok", is_reusable=True)
    first, second = _action_pair(team, pairing)

    assert _call(first) == "ok"
    second_result = _call(second)

    assert (RATE_LIMITED in second_result) is second_is_limited
    assert len(httpx_mock.get_requests()) == (1 if second_is_limited else 2)


@pytest.mark.django_db()
def test_over_limit_request_is_sent_in_log_only_mode(team, httpx_mock, settings):
    settings.RATE_LIMITS = ONE_PER_WINDOW
    settings.RATE_LIMIT_ENFORCE = False
    httpx_mock.add_response(url="https://example.com/test", text="ok", is_reusable=True)
    action = CustomActionFactory.create(team=team, auth_provider=_bearer_provider(team))

    assert [_call(action), _call(action)] == ["ok", "ok"]
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.django_db()
def test_over_limit_request_is_refused_with_a_tool_error(team, httpx_mock, settings):
    settings.RATE_LIMITS = ONE_PER_WINDOW
    settings.RATE_LIMIT_ENFORCE = True
    httpx_mock.add_response(url="https://example.com/test", text="ok")
    action = CustomActionFactory.create(team=team, auth_provider=_bearer_provider(team))
    _call(action)

    with mock.patch("apps.chat.agent.openapi_tool.custom_action_api_failure_notification") as notify:
        result = _call(action)

    assert result == "Rate limit exceeded for this API. The request was not sent."
    notify.assert_called_once()
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.django_db()
def test_request_is_sent_when_the_limiter_cannot_count(team, httpx_mock, settings):
    settings.RATE_LIMITS = ONE_PER_WINDOW
    settings.RATE_LIMIT_ENFORCE = True
    httpx_mock.add_response(url="https://example.com/test", text="ok")
    action = CustomActionFactory.create(team=team, auth_provider=_bearer_provider(team))

    with mock.patch("apps.utils.rate_limit._count", side_effect=ConnectionError("redis down")):
        assert _call(action) == "ok"
