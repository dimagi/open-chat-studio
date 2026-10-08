"""DB query-count ceilings per scenario. These run with the normal test suite.

Query counts do not vary with hardware, so unlike timings they can fail a PR on any increase. If a
change legitimately adds queries, raise the ceiling in the same PR.
"""

import pytest

from apps.benchmarks import scenarios
from apps.benchmarks.runner import count_queries

pytestmark = pytest.mark.django_db()


@pytest.mark.parametrize(
    ("history_size", "history_type", "ceiling"),
    [
        pytest.param(scenarios.HISTORY_NEW, None, 4, id="P1-H1-new-session"),
        pytest.param(scenarios.HISTORY_TYPICAL, "global", 4, id="P1-H2-typical-session"),
        pytest.param(scenarios.HISTORY_LONG, "global", 6, id="P1-H3-long-session"),
    ],
)
def test_floor_query_count(zero_latency_llm, history_size, history_type, ceiling):
    bench = scenarios.make_bench(history_size)
    runnable = scenarios.build_runnable(bench, scenarios.single_llm_nodes(bench, history_type=history_type))
    assert count_queries(runnable, bench) <= ceiling


def test_p2_linear_chain_query_count(zero_latency_llm):
    bench = scenarios.make_bench()
    runnable = scenarios.build_runnable(bench, scenarios.linear_chain_nodes(bench))
    assert count_queries(runnable, bench) <= 7
