"""Timing benchmarks for single nodes, each run as a start -> node -> end pipeline."""

import pytest

from apps.benchmarks import scenarios
from apps.benchmarks.runner import TIMING, invoke_once
from apps.pipelines.tests.utils import code_node, end_node, render_template_node, start_node

pytestmark = [pytest.mark.bench, pytest.mark.django_db(), TIMING]

TEMPLATE = "{% for i in range(20) %}item {{ i }}: {{ input }}\n{% endfor %}"
SCRIPT = """
def main(input, **kwargs):
    words = input.split()
    result = " ".join(words[::-1]).title()
    return f"{result} ({len(words)} words)"
"""


def test_n1_llm_plain_reply(benchmark, zero_latency_llm):
    bench = scenarios.make_bench()
    runnable = scenarios.build_runnable(bench, scenarios.single_llm_nodes(bench))
    benchmark(invoke_once, runnable, bench)


def test_n5_python_node(benchmark):
    bench = scenarios.make_bench()
    runnable = scenarios.build_runnable(bench, [start_node(), code_node(SCRIPT, name="code"), end_node()])
    benchmark(invoke_once, runnable, bench)


def test_n6_template_node(benchmark):
    bench = scenarios.make_bench()
    nodes = [start_node(), render_template_node(TEMPLATE, name="render"), end_node()]
    runnable = scenarios.build_runnable(bench, nodes)
    benchmark(invoke_once, runnable, bench)
