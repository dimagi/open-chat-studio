from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.benchmarks.scenarios import Bench
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.repository import ORMRepository


def invoke_once(runnable, bench: Bench, message: str = "Hello, how are you today?") -> dict:
    config = {"configurable": {"repo": ORMRepository(session=bench.session)}}
    return runnable.invoke(PipelineState(messages=[message], experiment_session=bench.session), config=config)


def count_queries(runnable, bench: Bench) -> int:
    """Number of DB queries one message costs. Independent of hardware, so it can gate PRs exactly."""
    with CaptureQueriesContext(connection) as ctx:
        invoke_once(runnable, bench)
    return len(ctx)
