import pytest

from apps.help.agents.prompt_improve import PromptImproveAgent, PromptImproveInput
from apps.help.evals.conftest import FIXTURES_DIR, load_fixtures, run_checks

cases = load_fixtures(FIXTURES_DIR / "prompt_improve.yml")


@pytest.mark.eval()
@pytest.mark.parametrize("case", cases, ids=lambda c: c["id"])
def test_prompt_improve(case):
    agent = PromptImproveAgent(input=PromptImproveInput(**case["input"]))
    result = agent.run()
    run_checks(result, case["checks"])
