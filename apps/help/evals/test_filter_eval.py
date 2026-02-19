import pytest

from apps.help.agents.filter import FilterAgent, FilterInput
from apps.help.evals.conftest import FIXTURES_DIR, load_fixtures, run_checks

cases = load_fixtures(FIXTURES_DIR / "filter.yml")


@pytest.mark.eval()
@pytest.mark.parametrize("case", cases, ids=lambda c: c["id"])
def test_filter(case):
    agent = FilterAgent(input=FilterInput(**case["input"]))
    result = agent.run()
    run_checks(result, case["checks"], output_field="filters")
