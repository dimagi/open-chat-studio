"""Tests for pr_risk_gate.py.

Run with: uv run pytest scripts/test_pr_risk_gate.py -v
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pr_risk_gate import (  # noqa: E402
    HIGH,
    LOW,
    MAX_CHANGED_LINES,
    MAX_FILES,
    MEDIUM,
    classify,
    glob_match,
    load_files,
    main,
)


def pr_file(filename, *, status="modified", additions=1, deletions=0, patch="+ ok"):
    return {
        "filename": filename,
        "status": status,
        "additions": additions,
        "deletions": deletions,
        "patch": patch,
    }


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        pytest.param("apps/chat/migrations/0001_initial.py", "apps/*/migrations/**", True, id="migration"),
        pytest.param("apps/chat/models.py", "apps/*/migrations/**", False, id="not-a-migration"),
        pytest.param("config/settings.py", "config/**", True, id="config-file"),
        pytest.param("config", "config/**", False, id="config-dir-itself"),
        pytest.param("README.md", "**/*.md", True, id="root-markdown"),
        pytest.param("docs/agents/domain.md", "**/*.md", True, id="nested-markdown"),
        pytest.param("apps/api/urls.py", "**/urls.py", True, id="nested-urls"),
        pytest.param("urls.py", "**/urls.py", True, id="root-urls"),
        pytest.param("apps/chat/urlsomething.py", "**/urls.py", False, id="urls-prefix-only"),
        pytest.param("docker-compose-dev.yml", "docker-compose*", True, id="compose-suffix"),
        pytest.param("Dockerfile.dev", "Dockerfile*", True, id="dockerfile-suffix"),
    ],
)
def test_glob_match(path, pattern, expected):
    assert glob_match(path, pattern) is expected


@pytest.mark.parametrize(
    ("filename", "reason_fragment"),
    [
        pytest.param("apps/chat/migrations/0042_add_field.py", "schema migration", id="migration"),
        pytest.param("apps/data_migrations/backfill.py", "data migration", id="data-migration"),
        pytest.param("config/settings.py", "Django configuration", id="settings"),
        pytest.param(".github/workflows/deploy.yml", "CI/CD", id="workflow"),
        pytest.param("uv.lock", "lockfile", id="lockfile"),
        pytest.param("apps/teams/models.py", "multi-tenancy", id="teams"),
        pytest.param("apps/api/serializers.py", "public API contract", id="api"),
        pytest.param("apps/service_providers/llm_service/main.py", "credentials", id="providers"),
        pytest.param("apps/experiments/urls.py", "URL routing", id="urls"),
        pytest.param("docs/adr/0049-usage-source.md", "architecture decision record", id="adr"),
        pytest.param("api-schemas/openapi.yml", "generated API schema", id="schema"),
    ],
)
def test_blocked_paths_are_high(filename, reason_fragment):
    verdict = classify([pr_file(filename)])
    assert verdict.risk == HIGH
    assert any(reason_fragment in reason for reason in verdict.reasons)


def test_editing_an_accepted_adr_beats_the_docs_allowlist():
    assert classify([pr_file("docs/adr/0001-example.md")]).risk == HIGH


def test_adding_a_new_adr_is_low():
    assert classify([pr_file("docs/adr/0065-new.md", status="added")]).risk == LOW


def test_deleting_an_adr_is_high():
    files = [pr_file("docs/adr/0001-example.md", status="removed", patch="-gone")]
    assert classify(files).risk == HIGH


def test_docs_only_is_low():
    files = [pr_file("docs/agents/domain.md"), pr_file("README.md")]
    assert classify(files).risk == LOW


def test_tests_only_is_low():
    files = [pr_file("apps/chat/tests/test_models.py"), pr_file("apps/trace/tests/test_views.py")]
    assert classify(files).risk == LOW


def test_docs_and_tests_together_are_low():
    assert classify([pr_file("docs/x.md"), pr_file("apps/chat/tests/test_models.py")]).risk == LOW


def test_application_code_is_medium():
    verdict = classify([pr_file("apps/experiments/views/experiment.py")])
    assert verdict.risk == MEDIUM
    assert "not on the low-risk allowlist" in verdict.reasons[0]


def test_one_blocked_file_outranks_an_otherwise_low_pr():
    files = [pr_file("docs/x.md"), pr_file("apps/chat/migrations/0001_initial.py")]
    assert classify(files).risk == HIGH


def test_a_net_loss_of_tests_is_high():
    patch = "@@\n-def test_old_behaviour():\n-    assert True\n"
    verdict = classify([pr_file("apps/chat/tests/test_models.py", patch=patch)])
    assert verdict.risk == HIGH
    assert verdict.reasons == ["the diff removes 1 more test(s) than it adds"]


def test_renaming_a_test_is_not_a_loss():
    patch = "@@\n-def test_old_name():\n+def test_new_name():\n"
    assert classify([pr_file("apps/chat/tests/test_models.py", patch=patch)]).risk == LOW


def test_tests_lost_in_one_file_are_offset_by_another():
    files = [
        pr_file("apps/chat/tests/test_models.py", patch="@@\n-def test_moved():\n"),
        pr_file("apps/chat/tests/test_views.py", patch="@@\n+def test_moved():\n"),
    ]
    assert classify(files).risk == LOW


@pytest.mark.parametrize(
    "added_line",
    [
        pytest.param('+@pytest.mark.skip(reason="flaky")', id="skip"),
        pytest.param("+@pytest.mark.xfail", id="xfail"),
        pytest.param('+    pytest.skip("not ready")', id="runtime-skip"),
    ],
)
def test_a_test_change_that_disables_a_test_is_high(added_line):
    verdict = classify([pr_file("apps/chat/tests/test_models.py", patch=f"@@\n{added_line}\n")])
    assert verdict.risk == HIGH
    assert "disables a test" in verdict.reasons[0]


def test_async_test_removal_is_counted():
    patch = "@@\n-async def test_streaming():\n"
    assert classify([pr_file("apps/chat/tests/test_models.py", patch=patch)]).risk == HIGH


def test_deleting_a_docs_file_stays_low():
    files = [pr_file("docs/obsolete.md", status="removed", additions=0, deletions=20, patch="-old")]
    assert classify(files).risk == LOW


def test_deleting_a_code_file_is_high():
    verdict = classify([pr_file("apps/chat/helpers.py", status="removed", patch="-gone")])
    assert verdict.risk == HIGH
    assert "file removed" in verdict.reasons[0]


def test_renaming_a_code_file_is_high():
    files = [{**pr_file("apps/chat/new.py", status="renamed"), "previous_filename": "apps/chat/old.py"}]
    assert classify(files).risk == HIGH


def test_a_rename_out_of_a_blocked_path_is_caught_by_the_source():
    files = [{**pr_file("docs/moved.md", status="renamed"), "previous_filename": "config/settings.py"}]
    verdict = classify(files)
    assert verdict.risk == HIGH
    assert any("config/settings.py" in reason for reason in verdict.reasons)


def test_too_many_files_is_medium():
    files = [pr_file(f"docs/page_{index}.md") for index in range(MAX_FILES + 1)]
    verdict = classify(files)
    assert verdict.risk == MEDIUM
    assert f"cap is {MAX_FILES}" in verdict.reasons[0]


def test_too_many_lines_is_medium():
    files = [pr_file("docs/big.md", additions=MAX_CHANGED_LINES + 1)]
    verdict = classify(files)
    assert verdict.risk == MEDIUM
    assert f"cap is {MAX_CHANGED_LINES}" in verdict.reasons[0]


def test_a_file_with_no_patch_cannot_be_low():
    files = [{"filename": "apps/chat/tests/test_models.py", "status": "modified", "additions": 5, "deletions": 0}]
    verdict = classify(files)
    assert verdict.risk == MEDIUM
    assert "diff too large to inspect" in verdict.reasons[0]


def test_untrusted_author_caps_a_low_pr_at_medium():
    verdict = classify([pr_file("docs/x.md")], untrusted=True)
    assert verdict.risk == MEDIUM
    assert "fork contributor" in verdict.reasons[-1]


def test_untrusted_author_does_not_soften_a_blocker():
    assert classify([pr_file("config/settings.py")], untrusted=True).risk == HIGH


def test_empty_file_list_is_medium():
    assert classify([]).risk == MEDIUM


def test_load_files_flattens_paginated_pages(tmp_path):
    payload = [[pr_file("docs/a.md")], [pr_file("docs/b.md")]]
    path = tmp_path / "files.json"
    path.write_text(json.dumps(payload))
    assert [entry["filename"] for entry in load_files(str(path))] == ["docs/a.md", "docs/b.md"]


def test_load_files_accepts_a_flat_list(tmp_path):
    path = tmp_path / "files.json"
    path.write_text(json.dumps([pr_file("docs/a.md")]))
    assert len(load_files(str(path))) == 1


def test_main_writes_github_output(tmp_path, monkeypatch, capsys):
    files_json = tmp_path / "files.json"
    files_json.write_text(json.dumps([pr_file("apps/chat/models.py")]))
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    assert main([str(files_json)]) == 0

    written = output.read_text()
    assert f"risk={MEDIUM}" in written
    assert "reasons<<REASONS_EOF" in written
    assert json.loads(capsys.readouterr().out)["risk"] == MEDIUM


def test_a_test_file_under_a_blocked_app_is_not_a_blocker():
    assert classify([pr_file("apps/teams/tests/test_membership.py")]).risk == LOW


def test_a_blocked_app_still_blocks_its_non_test_files():
    files = [pr_file("apps/teams/tests/test_membership.py"), pr_file("apps/teams/models.py")]
    verdict = classify(files)
    assert verdict.risk == HIGH
    assert verdict.reasons == ["apps/teams/models.py: team membership and the multi-tenancy boundary"]


def test_deleting_a_test_file_under_a_blocked_app_is_still_high():
    files = [pr_file("apps/teams/tests/test_membership.py", status="removed", patch="-gone")]
    assert classify(files).risk == HIGH


def test_a_test_named_file_outside_apps_does_not_escape_a_blocker():
    assert classify([pr_file(".github/workflows/test_deploy.py")]).risk == HIGH
    assert classify([pr_file("config/test_settings.py")]).risk == HIGH
