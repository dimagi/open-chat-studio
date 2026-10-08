"""Timing benchmarks for pipeline scenarios (see docs/developer_guides/testing/benchmarks.md).

Deselected by default; run with ``pytest -m bench apps/benchmarks``.
"""

import pytest

from apps.benchmarks import scenarios
from apps.benchmarks.runner import invoke_once

pytestmark = [pytest.mark.bench, pytest.mark.django_db()]


@pytest.mark.parametrize(
    ("history_size", "history_type"),
    [
        pytest.param(scenarios.HISTORY_NEW, None, id="P1-H1-new-session"),
        pytest.param(scenarios.HISTORY_TYPICAL, "global", id="P1-H2-typical-session"),
        pytest.param(scenarios.HISTORY_LONG, "global", id="P1-H3-long-session"),
    ],
)
def test_floor_by_history(benchmark, zero_latency_llm, history_size, history_type):
    bench = scenarios.make_bench(history_size)
    runnable = scenarios.build_runnable(bench, scenarios.single_llm_nodes(bench, history_type=history_type))
    benchmark(invoke_once, runnable, bench)


def test_p2_linear_chain(benchmark, zero_latency_llm):
    bench = scenarios.make_bench()
    runnable = scenarios.build_runnable(bench, scenarios.linear_chain_nodes(bench))
    benchmark(invoke_once, runnable, bench)


def test_p5_deep_chain(benchmark, zero_latency_llm):
    bench = scenarios.make_bench()
    runnable = scenarios.build_runnable(bench, scenarios.deep_chain_nodes(bench, length=25))
    benchmark(invoke_once, runnable, bench)
