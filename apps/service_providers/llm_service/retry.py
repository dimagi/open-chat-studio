"""
Retry configuration for LLM service calls.

This module provides centralized retry logic for handling rate limit errors
from various LLM providers (OpenAI, Anthropic, Google).

Two approaches are provided:
1. `get_retry_policy()` - Returns a LangGraph RetryPolicy for use with StateGraph.add_node()
2. `with_llm_retry()` - Wraps a Runnable with .with_retry() for direct invocations

Important: `with_llm_retry()` returns a RunnableRetry which loses chat-specific methods
like `bind_tools()`. For nodes using `create_agent()`, use `get_retry_policy()` instead.
"""

import anthropic
import openai
from google.api_core import exceptions as google_exceptions
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.runnables import Runnable
from langgraph.types import RetryPolicy

# Tuple of exception types that indicate rate limiting
RATE_LIMIT_EXCEPTIONS: tuple[type[Exception], ...] = (
    openai.RateLimitError,
    openai.InternalServerError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    google_exceptions.TooManyRequests,
    google_exceptions.ResourceExhausted,
)

# Default retry configuration
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_INITIAL_INTERVAL = 1.0  # seconds
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_MAX_INTERVAL = 60.0  # seconds


def should_retry_exception(exc: Exception) -> bool:
    """
    Determine if an exception should trigger a retry.

    Returns True for rate limit errors from supported providers.
    """
    if isinstance(exc, RATE_LIMIT_EXCEPTIONS):
        return True

    if hasattr(exc, "status_code"):
        return exc.status_code in (429, 503)

    return False


def get_retry_policy(
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_interval: float = DEFAULT_INITIAL_INTERVAL,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    max_interval: float = DEFAULT_MAX_INTERVAL,
) -> RetryPolicy:
    """
    Get a LangGraph RetryPolicy configured for rate limit handling.

    Use this with StateGraph.add_node(..., retry_policy=get_retry_policy())
    for pipeline nodes.

    Args:
        max_attempts: Maximum number of retry attempts (default: 3)
        initial_interval: Initial wait time between retries in seconds (default: 1.0)
        backoff_factor: Multiplier for wait time after each retry (default: 2.0)
        max_interval: Maximum wait time between retries in seconds (default: 60.0)

    Returns:
        RetryPolicy configured for rate limit handling
    """
    return RetryPolicy(
        max_attempts=max_attempts,
        initial_interval=initial_interval,
        backoff_factor=backoff_factor,
        max_interval=max_interval,
        jitter=True,
        retry_on=should_retry_exception,
    )


def with_llm_retry(
    runnable: Runnable,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Runnable:
    """
    Wrap a Runnable with retry logic for rate limit handling.

    WARNING: This returns a RunnableRetry which loses chat-specific methods
    like `bind_tools()`. Do NOT use this for models passed to `create_agent()`.
    Use `get_retry_policy()` with StateGraph.add_node() instead.

    Args:
        runnable: The Runnable (e.g., BaseChatModel) to wrap
        max_attempts: Maximum number of retry attempts (default: 3)

    Returns:
        Runnable wrapped with retry logic
    """
    return runnable.with_retry(
        retry_if_exception_type=RATE_LIMIT_EXCEPTIONS,
        wait_exponential_jitter=True,
        stop_after_attempt=max_attempts,
    )


def get_retry_middleware(
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_interval: float = DEFAULT_INITIAL_INTERVAL,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    max_interval: float = DEFAULT_MAX_INTERVAL,
):
    """
    Get a LangGraph ModelRetryMiddleware configured for rate limit handling.

    Use this with create_agent(..., middleware=[get_retry_middleware()]).

    Args:
        max_attempts: Maximum number of retry attempts (default: 3)
        initial_interval: Initial wait time between retries in seconds (default: 1.0)
        backoff_factor: Multiplier for wait time after each retry (default: 2.0)
        max_interval: Maximum wait time between retries in seconds (default: 60.0)

    Returns:
        ModelRetryMiddleware configured for rate limit handling
    """
    return ModelRetryMiddleware(
        max_retries=max_attempts,
        retry_on=should_retry_exception,
        backoff_factor=backoff_factor,
        initial_delay=initial_interval,
        max_delay=max_interval,
        on_failure="error",
        jitter=True,
    )
