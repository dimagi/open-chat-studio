import pytest

from apps.help.agents.code_generate import CodeGenerateAgent, CodeGenerateInput
from apps.help.evals.conftest import FIXTURES_DIR, load_fixtures, run_checks

cases = load_fixtures(FIXTURES_DIR / "code_generate.yml")


@pytest.mark.eval()
@pytest.mark.parametrize("case", cases, ids=lambda c: c["id"])
def test_code_generate(case):
    agent = CodeGenerateAgent(input=CodeGenerateInput(**case["input"]))
    result = agent.run()
    run_checks(result, case["checks"], output_field="code")
