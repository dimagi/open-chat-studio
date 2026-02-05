from unittest.mock import MagicMock

from apps.service_providers.llm_service.retry import (
    RATE_LIMIT_EXCEPTIONS,
    get_retry_policy,
    should_retry_exception,
    with_llm_retry,
)


class TestRateLimitExceptions:
    def test_openai_rate_limit_in_exceptions(self):
        import openai

        assert openai.RateLimitError in RATE_LIMIT_EXCEPTIONS

    def test_anthropic_rate_limit_in_exceptions(self):
        import anthropic

        assert anthropic.RateLimitError in RATE_LIMIT_EXCEPTIONS

    def test_google_resource_exhausted_in_exceptions(self):
        from google.api_core.exceptions import ResourceExhausted

        assert ResourceExhausted in RATE_LIMIT_EXCEPTIONS


class TestGetRetryPolicy:
    def test_returns_retry_policy(self):
        from langgraph.types import RetryPolicy

        policy = get_retry_policy()
        assert isinstance(policy, RetryPolicy)

    def test_default_max_attempts(self):
        policy = get_retry_policy()
        assert policy.max_attempts == 3

    def test_custom_max_attempts(self):
        policy = get_retry_policy(max_attempts=5)
        assert policy.max_attempts == 5


class TestShouldRetryException:
    def test_retries_openai_rate_limit(self):
        import openai

        exc = openai.RateLimitError("rate limited", response=MagicMock(), body=None)
        assert should_retry_exception(exc) is True

    def test_retries_anthropic_rate_limit(self):
        import anthropic

        exc = anthropic.RateLimitError("rate limited", response=MagicMock(), body=None)
        assert should_retry_exception(exc) is True

    def test_retries_google_resource_exhausted(self):
        from google.api_core.exceptions import ResourceExhausted

        exc = ResourceExhausted("rate limited")
        assert should_retry_exception(exc) is True

    def test_does_not_retry_other_exceptions(self):
        exc = ValueError("some error")
        assert should_retry_exception(exc) is False


class TestWithLlmRetry:
    def test_returns_runnable_retry(self):
        from langchain_core.runnables.retry import RunnableRetry

        mock_return = MagicMock(spec=RunnableRetry)
        mock_runnable = MagicMock()
        mock_runnable.with_retry = MagicMock(return_value=mock_return)

        result = with_llm_retry(mock_runnable)

        assert result is mock_return
        mock_runnable.with_retry.assert_called_once()
        call_kwargs = mock_runnable.with_retry.call_args.kwargs
        assert call_kwargs["retry_if_exception_type"] == RATE_LIMIT_EXCEPTIONS
        assert call_kwargs["stop_after_attempt"] == 3
        assert call_kwargs["wait_exponential_jitter"] is True
