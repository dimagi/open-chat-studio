import concurrent
import logging
from collections.abc import Callable
from concurrent.futures import Executor, Future
from contextlib import ExitStack, contextmanager
from functools import wraps
from typing import Any

from langgraph.constants import CONFIG_KEY_RUNNER_SUBMIT
from langgraph.pregel._executor import BackgroundExecutor, P, T

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
def patch_executor():
    """Monkeypatch the langchain executor to run tasks in the current thread.
    This is used for pipeline tests where the DB transaction is not committed."""
    from langchain_core.runnables import config

    original = config.ContextThreadPoolExecutor
    config.ContextThreadPoolExecutor = CurrentThreadExecutor
    try:
        yield
    finally:
        config.ContextThreadPoolExecutor = original


class DjangoLangGraphRunner:
    """
    High-level runner that manages the executor lifecycle for you.

    This is the recommended way to use Django-safe execution with LangGraph.

    Example:
        with DjangoLangGraphRunner(max_workers=4) as runner:
            result = runner.invoke(app, input_data)
    """

    def __init__(self, max_workers: int | None = None):
        """
        Initialize the runner.

        Args:
            max_workers: Maximum number of workers
            use_processes: If True, use process pool instead of thread pool
        """
        self.max_workers = max_workers
        self.executor = None
        self.stack = ExitStack()
        self.executor = DjangoSafeBackgroundExecutor({"configurable": {"max_concurrency": self.max_workers}})
        self.submit = self.stack.enter_context(self.executor)

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
        if self.executor is None:
            raise RuntimeError("Runner has been shut down")

        run_config = {CONFIG_KEY_RUNNER_SUBMIT: lambda: self.submit}
        if config:
            run_config.update(config)

        return app.invoke(input_data, config=run_config)

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
        if self.executor is None:
            raise RuntimeError("Runner has been shut down")

        run_config = {CONFIG_KEY_RUNNER_SUBMIT: lambda: self.submit}
        if config:
            run_config.update(config)

        yield from app.stream(input_data, config=run_config)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stack.__exit__(exc_type, exc_val, exc_tb)


class DjangoSafeBackgroundExecutor(BackgroundExecutor):
    """
    A wrapper around BackgroundExecutor that ensures all submitted tasks
    properly manage Django database connections.

    This executor automatically wraps all submitted functions with Django
    connection cleanup logic.
    """

    def submit(  # type: ignore[valid-type]
        self,
        fn: Callable[P, T],
        *args: P.args,
        __name__: str | None = None,  # currently not used in sync version
        __cancel_on_exit__: bool = False,  # for sync, can cancel only if not started
        __reraise_on_exit__: bool = True,
        __next_tick__: bool = False,
        **kwargs: P.kwargs,
    ) -> concurrent.futures.Future[T]:
        # Wrap the function with Django connection cleanup
        wrapped_fn = _django_db_cleanup_wrapper(fn)
        return super().submit(
            wrapped_fn,
            *args,
            __name__=__name__,
            __cancel_on_exit__=__cancel_on_exit__,
            __reraise_on_exit__=__reraise_on_exit__,
            __next_tick__=__next_tick__,
            **kwargs,
        )


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
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            logger.error(f"Error in Django-wrapped task: {e}")
            raise
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
