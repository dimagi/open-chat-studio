"""Tests for auto_merge_low_risk.py.

Run with: uv run pytest scripts/test_auto_merge_low_risk.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import auto_merge_low_risk  # noqa: E402
from auto_merge_low_risk import (  # noqa: E402
    REQUIRED_CHECKS,
    latest_check_runs,
    latest_review_states,
)

REPO = "dimagi/open-chat-studio"
LOW = "risk:low"


def find_blockers(pull, check_runs, reviews, repo=REPO, *, risk=LOW):
    """The gate's own verdict defaults to low here so each test varies one thing."""
    return auto_merge_low_risk.find_blockers(pull, check_runs, reviews, repo, recomputed_risk=risk)


def pull(**overrides):
    base = {
        "number": 1,
        "title": "Fix a typo",
        "draft": False,
        "labels": [{"name": "risk:low"}],
        "base": {"ref": "main"},
        "head": {"sha": "abc123", "repo": {"full_name": REPO}},
        "mergeable": True,
        "mergeable_state": "clean",
    }
    return {**base, **overrides}


def passing_checks(*extra):
    runs = [{"name": name, "status": "completed", "conclusion": "success"} for name in REQUIRED_CHECKS]
    return [*runs, *extra]


def check(name, *, status="completed", conclusion="success", started_at="2026-09-16T00:00:00Z"):
    return {"name": name, "status": status, "conclusion": conclusion, "started_at": started_at}


def test_a_clean_low_risk_pull_request_has_no_blockers():
    assert find_blockers(pull(), passing_checks(), [], REPO) == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        pytest.param({"draft": True}, "it is a draft", id="draft"),
        pytest.param({"labels": []}, "not labelled risk:low", id="unlabelled"),
        pytest.param({"labels": [{"name": "risk:medium"}]}, "not labelled risk:low", id="medium"),
        pytest.param({"labels": [{"name": "risk:low"}, {"name": "wip"}]}, "carries the wip label", id="wip"),
        pytest.param({"base": {"ref": "release"}}, "targets release", id="wrong-base"),
        pytest.param({"mergeable": False}, "mergeable=False", id="not-mergeable"),
        pytest.param({"mergeable": None}, "mergeable=None", id="mergeability-unknown"),
        pytest.param({"mergeable_state": "dirty"}, "mergeable_state is dirty", id="conflicted"),
        pytest.param({"mergeable_state": "blocked"}, "mergeable_state is blocked", id="protection"),
        pytest.param({"mergeable_state": "behind"}, "mergeable_state is behind", id="behind"),
    ],
)
def test_pull_request_state_blocks(overrides, fragment):
    blockers = find_blockers(pull(**overrides), passing_checks(), [], REPO)
    assert any(fragment in blocker for blocker in blockers)


def test_a_fork_head_blocks():
    forked = pull(head={"sha": "abc123", "repo": {"full_name": "someone/open-chat-studio"}})
    assert "the head branch is on a fork" in find_blockers(forked, passing_checks(), [], REPO)


def test_a_deleted_head_repo_blocks():
    assert "the head branch is on a fork" in find_blockers(
        pull(head={"sha": "abc", "repo": None}), passing_checks(), [], REPO
    )


@pytest.mark.parametrize("missing", REQUIRED_CHECKS)
def test_a_missing_required_check_blocks(missing):
    runs = [run for run in passing_checks() if run["name"] != missing]
    blockers = find_blockers(pull(), runs, [], REPO)
    assert f"the required check '{missing}' has not reported on this commit" in blockers


def test_no_checks_at_all_blocks():
    blockers = find_blockers(pull(), [], [], REPO)
    assert len(blockers) == len(REQUIRED_CHECKS)


@pytest.mark.parametrize(
    ("conclusion", "status"),
    [
        pytest.param("failure", "completed", id="failure"),
        pytest.param("skipped", "completed", id="skipped"),
        pytest.param("neutral", "completed", id="neutral"),
        pytest.param(None, "in_progress", id="in-progress"),
    ],
)
def test_a_required_check_must_be_success_not_merely_not_failing(conclusion, status):
    runs = [check(name) for name in REQUIRED_CHECKS[1:]]
    runs.append(check(REQUIRED_CHECKS[0], status=status, conclusion=conclusion))
    blockers = find_blockers(pull(), runs, [], REPO)
    assert any(REQUIRED_CHECKS[0] in blocker for blocker in blockers)


def test_a_failing_other_check_blocks():
    runs = passing_checks(check("Lint and Test", conclusion="failure"))
    assert "the check 'Lint and Test' concluded failure" in find_blockers(pull(), runs, [], REPO)


def test_a_pending_other_check_blocks():
    runs = passing_checks(check("Lint and Test", status="in_progress", conclusion=None))
    assert "the check 'Lint and Test' is still in_progress" in find_blockers(pull(), runs, [], REPO)


@pytest.mark.parametrize("conclusion", ["success", "skipped", "neutral"])
def test_an_inapplicable_other_check_does_not_block(conclusion):
    runs = passing_checks(check("Build components", conclusion=conclusion))
    assert find_blockers(pull(), runs, [], REPO) == []


def test_a_rerun_supersedes_the_earlier_failure():
    runs = passing_checks(
        check("Lint and Test", conclusion="failure", started_at="2026-09-16T10:00:00Z"),
        check("Lint and Test", conclusion="success", started_at="2026-09-16T11:00:00Z"),
    )
    assert find_blockers(pull(), runs, [], REPO) == []


def test_a_rerun_that_newly_fails_blocks():
    runs = passing_checks(
        check("Lint and Test", conclusion="success", started_at="2026-09-16T10:00:00Z"),
        check("Lint and Test", conclusion="failure", started_at="2026-09-16T11:00:00Z"),
    )
    assert "the check 'Lint and Test' concluded failure" in find_blockers(pull(), runs, [], REPO)


def test_latest_check_runs_keeps_the_most_recent_per_name():
    runs = [
        check("a", conclusion="failure", started_at="2026-09-16T10:00:00Z"),
        check("a", conclusion="success", started_at="2026-09-16T11:00:00Z"),
        check("b", started_at="2026-09-16T09:00:00Z"),
    ]
    latest = latest_check_runs(runs)
    assert latest["a"]["conclusion"] == "success"
    assert set(latest) == {"a", "b"}


def test_requested_changes_block():
    reviews = [{"user": {"login": "reviewer"}, "state": "CHANGES_REQUESTED"}]
    assert "reviewer requested changes" in find_blockers(pull(), passing_checks(), reviews, REPO)


def test_requested_changes_later_approved_do_not_block():
    reviews = [
        {"user": {"login": "reviewer"}, "state": "CHANGES_REQUESTED"},
        {"user": {"login": "reviewer"}, "state": "APPROVED"},
    ]
    assert find_blockers(pull(), passing_checks(), reviews, REPO) == []


def test_an_approval_followed_by_changes_requested_blocks():
    reviews = [
        {"user": {"login": "reviewer"}, "state": "APPROVED"},
        {"user": {"login": "reviewer"}, "state": "CHANGES_REQUESTED"},
    ]
    assert "reviewer requested changes" in find_blockers(pull(), passing_checks(), reviews, REPO)


def test_a_plain_comment_does_not_clear_requested_changes():
    reviews = [
        {"user": {"login": "reviewer"}, "state": "CHANGES_REQUESTED"},
        {"user": {"login": "reviewer"}, "state": "COMMENTED"},
    ]
    assert "reviewer requested changes" in find_blockers(pull(), passing_checks(), reviews, REPO)


def test_one_reviewer_cannot_clear_another_reviewers_objection():
    reviews = [
        {"user": {"login": "alice"}, "state": "CHANGES_REQUESTED"},
        {"user": {"login": "bob"}, "state": "APPROVED"},
    ]
    assert "alice requested changes" in find_blockers(pull(), passing_checks(), reviews, REPO)


def test_latest_review_states_ignores_pending():
    reviews = [
        {"user": {"login": "alice"}, "state": "APPROVED"},
        {"user": {"login": "alice"}, "state": "PENDING"},
    ]
    assert latest_review_states(reviews) == {"alice": "APPROVED"}


def test_blockers_accumulate():
    blockers = find_blockers(pull(draft=True, mergeable_state="dirty"), [], [], REPO)
    assert len(blockers) == 2 + len(REQUIRED_CHECKS)


def test_latest_check_runs_breaks_a_timestamp_tie_on_id():
    runs = [
        {**check("a", conclusion="failure"), "id": 2},
        {**check("a", conclusion="success"), "id": 1},
    ]
    assert latest_check_runs(runs)["a"]["conclusion"] == "failure"


def test_merge_pins_the_head_commit(monkeypatch):
    calls = []
    monkeypatch.setattr(auto_merge_low_risk, "gh", lambda *args: calls.append(args) or "")
    auto_merge_low_risk.merge(REPO, 7, "merge", "abc123")
    assert "sha=abc123" in calls[0]


@pytest.mark.parametrize("risk", ["risk:medium", "risk:high"])
def test_the_recomputed_gate_overrides_the_label(risk):
    blockers = find_blockers(pull(), passing_checks(), [], risk=risk)
    assert blockers == [f"the gate re-runs this as {risk}, whatever the label says"]


def test_a_hand_applied_label_cannot_merge_a_medium_pull_request():
    """Anyone with write access can apply `risk:low`; only the gate's own verdict counts."""
    labelled = pull(labels=[{"name": "risk:low"}])
    assert find_blockers(labelled, passing_checks(), [], risk="risk:medium") != []
