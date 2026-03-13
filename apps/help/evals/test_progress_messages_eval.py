import pytest

from apps.help.agents.progress_messages import ProgressMessagesAgent, ProgressMessagesInput
from apps.help.evals.conftest import FIXTURES_DIR, load_fixtures, run_checks

cases = load_fixtures(FIXTURES_DIR / "progress_messages.yml")


@pytest.mark.eval()
@pytest.mark.parametrize("case", cases, ids=lambda c: c["id"])
def test_progress_messages(case):
    agent = ProgressMessagesAgent(input=ProgressMessagesInput(**case["input"]))
    result = agent.run()
    run_checks(result, case["checks"], output_field="messages")
