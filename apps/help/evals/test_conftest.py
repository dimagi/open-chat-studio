from pathlib import Path
from unittest import mock

import pytest

from apps.help.evals.conftest import JudgeResult, llm_judge, load_fixtures, run_checks


class TestLoadFixtures:
    def test_loads_yaml_file(self, tmp_path):
        fixture_file = tmp_path / "test.yml"
        fixture_file.write_text("- id: case1\n  input:\n    query: hello\n  checks:\n    - type: syntax\n")
        cases = load_fixtures(fixture_file)
        assert len(cases) == 1
        assert cases[0]["id"] == "case1"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_fixtures(Path("/nonexistent/file.yml"))


class TestLlmJudge:
    @mock.patch("apps.help.evals.conftest.build_system_agent")
    def test_returns_pass_and_reason(self, mock_build):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {"structured_response": JudgeResult(passed=True, reason="Looks good")}
        mock_build.return_value = mock_agent

        passed, reason = llm_judge("some output", "must be good")
        assert passed is True
        assert reason == "Looks good"

    @mock.patch("apps.help.evals.conftest.build_system_agent")
    def test_returns_fail(self, mock_build):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {"structured_response": JudgeResult(passed=False, reason="Not good enough")}
        mock_build.return_value = mock_agent

        passed, reason = llm_judge("bad output", "must be good")
        assert passed is False


class TestRunChecks:
    def test_passing_checks(self):
        code = "def main(input: str, **kwargs) -> str:\n    return input"
        checks = [{"type": "syntax"}, {"type": "has_main"}]
        # Should not raise
        run_checks(code, checks, output_field="code")

    def test_failing_check_raises(self):
        code = "def foo():\n    pass"
        checks = [{"type": "syntax"}, {"type": "has_main"}]
        with pytest.raises(AssertionError, match="has_main"):
            run_checks(code, checks, output_field="code")
