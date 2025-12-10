"""
This module contains modified thread executor classes which are used to patch langgraph.

This approach is used instead of using the `CONFIG_KEY_RUNNER_SUBMIT` configuration parameter to ensure
that all threaded operations are patched, not only Pregel tasks.
"""

import logging
from collections.abc import Callable, Generator, Iterable, Iterator
from concurrent.futures import Executor, Future
from contextlib import contextmanager
from functools import wraps
from typing import Any

from langchain_core.runnables.config import ContextThreadPoolExecutor, P, T

logger = logging.getLogger(__name__)


class CurrentThreadExecutor(Executor):
    """Fake thread pool executor that runs tasks in the current thread."""

    def __init__(self, *args, **kwargs):
        super().__init__()

    def submit(self, fn, /, *args, **kwargs):
        future = Future()
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)

        return future


@contextmanager
def patch_executor(executor: type[Executor]) -> Generator[None, Any, None]:
    """Monkeypatch the langchain executor to run tasks in the current thread.
    This is used for pipeline tests where the DB transaction is not committed."""
    from langchain_core.runnables import config

    original = config.ContextThreadPoolExecutor
    config.ContextThreadPoolExecutor = executor
    try:
        yield
    finally:
        config.ContextThreadPoolExecutor = original


class DjangoLangGraphRunner:
    """
    High-level runner that manages the executor lifecycle for you.

    This is the recommended way to use Django-safe execution with LangGraph.

    Example:
        runner = DjangoLangGraphRunner(executor_class)
        result = runner.invoke(app, input_data)
    """

    def __init__(self, executor: type[Executor]):
        """
        Initialize the runner.

        Args:
            executor: Executor class to patch langgraph
        """
        self.executor = executor

    def invoke(self, app, input_data: dict, config: dict | None = None) -> Any:
        """
        Invoke a LangGraph app with Django-safe execution.

        Args:
            app: Compiled LangGraph application
            input_data: Input data for the graph
            config: Additional configuration (will be merged with runner config)

        Returns:
            Result from the graph execution
        """
        with patch_executor(self.executor):
            return app.invoke(input_data, config=config)

    def stream(self, app, input_data: dict, config: dict | None = None):
        """
        Stream results from a LangGraph app with Django-safe execution.

        Args:
            app: Compiled LangGraph application
            input_data: Input data for the graph
            config: Additional configuration (will be merged with runner config)

        Yields:
            Stream of results from the graph execution
        """
        with patch_executor(self.executor):
            yield from app.stream(input_data, config=config)


class DjangoSafeContextThreadPoolExecutor(ContextThreadPoolExecutor):
    """Thread pool executor that wraps the target function with Django database connection handling."""

    def submit(  # type: ignore[override]
        self,
        func: Callable[P, T],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> Future[T]:
        wrapped_fn = _django_db_cleanup_wrapper(func)
        return super().submit(wrapped_fn, *args, **kwargs)

    def map(self, fn: Callable[..., T], *iterables: Iterable[Any], **kwargs: Any) -> Iterator[T]:
        wrapped_fn = _django_db_cleanup_wrapper(fn)
        return super().map(wrapped_fn, *iterables, **kwargs)


def _django_db_cleanup_wrapper(func: Callable) -> Callable:
    """
    Wraps a function to ensure Django database connections are properly managed.

    This wrapper:
    1. Closes old/stale connections before executing the task
    2. Ensures connections are closed after task completion
    3. Handles exceptions gracefully

    Args:
        func: The function to wrap

    Returns:
        Wrapped function with Django DB connection management
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        # Close any stale connections from previous tasks
        close_db_connections()

        try:
            # Execute the actual task
            return func(*args, **kwargs)
        finally:
            # Clean up connections after task completion
            # This is critical to prevent connection leaks
            close_db_connections()

    return wrapper


def close_db_connections():
    from django.db import connections

    try:
        connections.close_all()
    except Exception as cleanup_error:
        logger.warning(f"Error closing connection: {cleanup_error}")
