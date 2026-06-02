"""Tests for run-level tags_archived tracking: archive-on-supersede, undo archiving, eligibility."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.evaluations.models import (
    ConditionType,
    EvaluationMode,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationRunType,
)
from apps.evaluations.tagging import (
    apply_rules_to_result,
    archive_superseded_runs,
    can_undo_tags,
    undo_run_tags,
)
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
    EvaluatorTagRuleFactory,
)
from apps.utils.factories.team import TeamFactory


@pytest.fixture()
def team(db):
    return TeamFactory.create()


def _stamp(run, delta):
    """Force created_at/finished_at to now()+delta (bypasses auto_now_add)."""
    now = timezone.now()
    EvaluationRun.objects.filter(pk=run.pk).update(created_at=now + delta, finished_at=now + delta)
    run.refresh_from_db()


def _make_setup(team, mode=EvaluationMode.MESSAGE):
    evaluator = EvaluatorFactory.create(team=team, evaluation_mode=mode)
    rule_neg = EvaluatorTagRuleFactory.create(
        team=team,
        evaluator=evaluator,
        field_name="sentiment",
        condition_type=ConditionType.EQUALS,
        condition_value={"value": "negative"},
        tag__name="bad",
    )
    rule_pos = EvaluatorTagRuleFactory.create(
        team=team,
        evaluator=evaluator,
        field_name="sentiment",
        condition_type=ConditionType.EQUALS,
        condition_value={"value": "positive"},
        tag__name="good",
    )
    message = EvaluationMessageFactory.create(create_chat_messages=True)
    dataset = EvaluationDatasetFactory.create(team=team, messages=[message])
    config = EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[evaluator])
    return config, evaluator, rule_neg, rule_pos, message


def _make_run(config, evaluator, message, output, run_type=EvaluationRunType.FULL):
    run = EvaluationRunFactory.create(
        team=config.team,
        config=config,
        status=EvaluationRunStatus.COMPLETED,
        type=run_type,
        finished_at=timezone.now(),
    )
    result = EvaluationResultFactory.create(
        team=config.team, evaluator=evaluator, message=message, run=run, output=output
    )
    apply_rules_to_result(result, evaluator, message)
    return run


class TestArchiveSupersededRuns:
    def test_prior_run_archived_when_new_full_run_completes(self, team):
        config, evaluator, rule_neg, rule_pos, message = _make_setup(team)

        run1 = _make_run(config, evaluator, message, {"result": {"sentiment": "negative"}})
        _stamp(run1, timedelta(hours=-1))
        run2 = _make_run(config, evaluator, message, {"result": {"sentiment": "positive"}})

        archive_superseded_runs(run2)

        run1.refresh_from_db()
        run2.refresh_from_db()
        assert run1.tags_archived is True
        assert run2.tags_archived is False

    def test_preview_run_is_no_op(self, team):
        config, evaluator, rule_neg, _, message = _make_setup(team)
        run1 = _make_run(config, evaluator, message, {"result": {"sentiment": "negative"}})
        _stamp(run1, timedelta(hours=-1))
        preview = _make_run(
            config, evaluator, message, {"result": {"sentiment": "positive"}}, run_type=EvaluationRunType.PREVIEW
        )

        archive_superseded_runs(preview)

        run1.refresh_from_db()
        assert run1.tags_archived is False  # preview never supersedes

    def test_delta_run_archives_nothing(self, team):
        """A DELTA run only adds disjoint new-session tags; it supersedes no prior run."""
        config, evaluator, rule_neg, _, message = _make_setup(team)
        run1 = _make_run(config, evaluator, message, {"result": {"sentiment": "negative"}})
        _stamp(run1, timedelta(hours=-1))

        other_message = EvaluationMessageFactory.create(create_chat_messages=True)
        config.dataset.messages.add(other_message)
        delta = _make_run(
            config, evaluator, other_message, {"result": {"sentiment": "negative"}}, run_type=EvaluationRunType.DELTA
        )
        delta.scoped_messages.add(other_message)

        archive_superseded_runs(delta)

        run1.refresh_from_db()
        delta.refresh_from_db()
        assert run1.tags_archived is False
        assert delta.tags_archived is False

    def test_full_run_archives_prior_full_and_its_deltas(self, team):
        config, evaluator, rule_neg, _, message = _make_setup(team)
        full1 = _make_run(config, evaluator, message, {"result": {"sentiment": "negative"}})
        _stamp(full1, timedelta(hours=-2))

        other_message = EvaluationMessageFactory.create(create_chat_messages=True)
        config.dataset.messages.add(other_message)
        delta = _make_run(
            config, evaluator, other_message, {"result": {"sentiment": "negative"}}, run_type=EvaluationRunType.DELTA
        )
        delta.scoped_messages.add(other_message)
        _stamp(delta, timedelta(hours=-1))

        full2 = _make_run(config, evaluator, message, {"result": {"sentiment": "positive"}})
        archive_superseded_runs(full2)

        full1.refresh_from_db()
        delta.refresh_from_db()
        full2.refresh_from_db()
        assert full1.tags_archived is True
        assert delta.tags_archived is True
        assert full2.tags_archived is False


class TestUndoArchiving:
    def test_undo_archives_current_and_reactivates_prior(self, team):
        config, evaluator, rule_neg, rule_pos, message = _make_setup(team)

        run1 = _make_run(config, evaluator, message, {"result": {"sentiment": "negative"}})
        _stamp(run1, timedelta(hours=-1))
        run2 = _make_run(config, evaluator, message, {"result": {"sentiment": "positive"}})
        archive_superseded_runs(run2)  # run1 -> archived, run2 -> active

        undo_run_tags(run2)

        run1.refresh_from_db()
        run2.refresh_from_db()
        assert run2.tags_archived is True
        assert run1.tags_archived is False

    def test_undo_with_no_prior_run_archives_current(self, team):
        config, evaluator, rule_neg, _, message = _make_setup(team)
        run1 = _make_run(config, evaluator, message, {"result": {"sentiment": "negative"}})

        undo_run_tags(run1)

        run1.refresh_from_db()
        assert run1.tags_archived is True


class TestCanUndoTags:
    def test_latest_full_run_is_undoable(self, team):
        config, evaluator, rule_neg, _, message = _make_setup(team)
        run = _make_run(config, evaluator, message, {"result": {"sentiment": "negative"}})
        assert can_undo_tags(run) is True

    def test_non_latest_full_run_not_undoable(self, team):
        config, evaluator, rule_neg, rule_pos, message = _make_setup(team)
        run1 = _make_run(config, evaluator, message, {"result": {"sentiment": "negative"}})
        _stamp(run1, timedelta(hours=-1))
        _make_run(config, evaluator, message, {"result": {"sentiment": "positive"}})
        assert can_undo_tags(run1) is False

    def test_delta_run_not_undoable(self, team):
        config, evaluator, rule_neg, _, message = _make_setup(team)
        delta = _make_run(
            config, evaluator, message, {"result": {"sentiment": "negative"}}, run_type=EvaluationRunType.DELTA
        )
        assert can_undo_tags(delta) is False

    def test_preview_run_not_undoable(self, team):
        config, evaluator, rule_neg, _, message = _make_setup(team)
        preview = _make_run(
            config, evaluator, message, {"result": {"sentiment": "negative"}}, run_type=EvaluationRunType.PREVIEW
        )
        assert can_undo_tags(preview) is False

    def test_already_undone_run_not_undoable(self, team):
        config, evaluator, rule_neg, rule_pos, message = _make_setup(team)
        run1 = _make_run(config, evaluator, message, {"result": {"sentiment": "negative"}})
        _stamp(run1, timedelta(hours=-1))
        run2 = _make_run(config, evaluator, message, {"result": {"sentiment": "positive"}})
        archive_superseded_runs(run2)

        assert can_undo_tags(run2) is True
        undo_run_tags(run2)
        run1.refresh_from_db()
        run2.refresh_from_db()
        # After undo: run2 is latest-but-archived, run1 is active-but-not-latest -> neither undoable
        assert can_undo_tags(run2) is False
        assert can_undo_tags(run1) is False

    def test_processing_run_not_undoable(self, team):
        config, evaluator, rule_neg, _, message = _make_setup(team)
        run = EvaluationRunFactory.create(
            team=team, config=config, status=EvaluationRunStatus.PROCESSING, type=EvaluationRunType.FULL
        )
        assert can_undo_tags(run) is False
