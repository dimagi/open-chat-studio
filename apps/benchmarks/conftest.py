import pytest

from apps.benchmarks import fakes


@pytest.fixture()
def zero_latency_llm():
    with fakes.fake_llm_provider(fakes.zero_latency()) as service:
        yield service
