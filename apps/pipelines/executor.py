from concurrent.futures import Executor, Future
from contextlib import contextmanager


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
