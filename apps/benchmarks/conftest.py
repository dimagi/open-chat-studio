import logging

import pytest

from apps.benchmarks import fakes


@pytest.fixture(autouse=True)
def quiet_ocs_logging(caplog):
    """Keep pytest's log capture from formatting and storing a DEBUG record on every round."""
    caplog.set_level(logging.WARNING, logger="ocs")


@pytest.fixture()
def zero_latency_llm():
    with fakes.fake_llm_provider(fakes.zero_latency()) as service:
        yield service
