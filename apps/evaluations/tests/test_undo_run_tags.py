"""DB integration tests for undo_run_tags and its prior-run lookup."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.annotations.models import CustomTaggedItem
from apps.evaluations.models import (
    AppliedTag,
    ConditionType,
    EvaluationMode,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationRunType,
)
from apps.evaluations.tagging import (
    _latest_completed_full_run,
    _restore_set_for_run,
    apply_rules_to_result,
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
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory


@pytest.fixture()
def team(db):
    return TeamFactory.create()


def _stamp(run, delta, finished_delta=None):
    """Force-set created_at (and finished_at) on a run via QuerySet.update() (bypasses auto_now_add).

    `created_at` is set to now() + `delta`. `finished_at` is set to now() + `finished_delta`,
    defaulting to `delta` so the two move together unless a test deliberately models an
    overlapping run that finished at a different time than it was created.
    """
    now = timezone.now()
    if finished_delta is None:
        finished_delta = delta
    EvaluationRun.objects.filter(pk=run.pk).update(created_at=now + delta, finished_at=now + finished_delta)
    run.refresh_from_db()


class TestUndoRunTags:
    # ---- helper ------------------------------------------------------------

    def _make_setup(self, team, mode=EvaluationMode.MESSAGE):
        """Return (config, evaluator, rule_neg, rule_pos, message) all on the same team.

        Both rules target the same `sentiment` field but with opposite values so
        tests can verify tag switches cleanly.
        """
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
        if mode == EvaluationMode.MESSAGE:
            message = EvaluationMessageFactory.create(create_chat_messages=True)
        else:
            session = ExperimentSessionFactory.create(team=team)
            message = EvaluationMessageFactory.create(session=session)

        dataset = EvaluationDatasetFactory.create(team=team, messages=[message])
        config = EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[evaluator])
        return config, evaluator, rule_neg, rule_pos, message

    def _make_run(self, config, output, rule_that_fires, message):
        """Create a COMPLETED FULL run, apply the tag rule, return the run."""
        run = EvaluationRunFactory.create(
            team=config.team,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.FULL,
            finished_at=timezone.now(),
        )
        result = EvaluationResultFactory.create(
            team=config.team,
            evaluator=rule_that_fires.evaluator,
            message=message,
            run=run,
            output=output,
        )
        apply_rules_to_result(result, rule_that_fires.evaluator, message)
        return run

    # ---- tests -------------------------------------------------------------

    def test_preview_run_is_no_op(self, team):
        """PREVIEW runs must never be touched by undo."""
        config, evaluator, rule_neg, _, message = self._make_setup(team)
        run = EvaluationRunFactory.create(
            team=team,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.PREVIEW,
        )
        result = EvaluationResultFactory.create(
            team=team,
            evaluator=evaluator,
            message=message,
            run=run,
            output={"result": {"sentiment": "negative"}},
        )
        apply_rules_to_result(result, evaluator, message)

        chat_message = message.expected_output_chat_message
        assert chat_message.tags.filter(pk=rule_neg.tag_id).exists()

        undo_run_tags(run)

        # Still has the tag; PREVIEW was a no-op
        assert chat_message.tags.filter(pk=rule_neg.tag_id).exists()

    def test_no_previous_run_removes_current_tags(self, team):
        """With no prior run, undo simply strips the current run's applied tags."""
        config, evaluator, rule_neg, _, message = self._make_setup(team)
        run = self._make_run(config, {"result": {"sentiment": "negative"}}, rule_neg, message)

        chat_message = message.expected_output_chat_message
        assert chat_message.tags.filter(pk=rule_neg.tag_id).exists()

        undo_run_tags(run)

        assert not chat_message.tags.filter(pk=rule_neg.tag_id).exists()
        # AppliedTag audit records are NOT deleted
        assert AppliedTag.objects.filter(evaluation_result__run=run).exists()

    def test_restores_previous_run_tags_and_removes_current(self, team):
        """
        Simulate two successive runs where the tag switches:
          prev run  -> "bad" (negative) applied
          curr run  -> "good" (positive) applied; reverse_stale_tags would have removed "bad"

        After undo: "bad" is back, "good" is gone.
        """
        config, evaluator, rule_neg, rule_pos, message = self._make_setup(team)

        # Build previous run (applied "bad")
        prev_run = self._make_run(config, {"result": {"sentiment": "negative"}}, rule_neg, message)
        _stamp(prev_run, timedelta(hours=-1))

        # Build current run (applied "good")
        # No _stamp needed: prev_run is at now()-1h, so curr_run's natural created_at is later.
        curr_run = self._make_run(config, {"result": {"sentiment": "positive"}}, rule_pos, message)

        # Simulate what reverse_stale_tags would have done: remove "bad" from target
        chat_message = message.expected_output_chat_message
        CustomTaggedItem.objects.filter(object_id=chat_message.pk, tag_id=rule_neg.tag_id).delete()

        # Precondition: only "good" is currently on the message
        assert chat_message.tags.filter(pk=rule_pos.tag_id).exists()
        assert not chat_message.tags.filter(pk=rule_neg.tag_id).exists()

        undo_run_tags(curr_run)

        # After undo: "bad" restored, "good" removed
        assert chat_message.tags.filter(pk=rule_neg.tag_id).exists()
        assert not chat_message.tags.filter(pk=rule_pos.tag_id).exists()

    def test_no_tag_rules_is_no_op(self, team):
        """Configs with evaluators that have no tag rules do nothing."""
        evaluator = EvaluatorFactory.create(team=team)  # no EvaluatorTagRules created
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        dataset = EvaluationDatasetFactory.create(team=team, messages=[message])
        config = EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[evaluator])
        run = EvaluationRunFactory.create(
            team=team,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.FULL,
        )
        # Should return early without touching DB
        undo_run_tags(run)
        assert CustomTaggedItem.objects.count() == 0

    def test_undo_on_delta_run_is_a_no_op(self, team):
        """DELTA runs are never directly undoable; undo_run_tags must leave their tags alone."""
        config, evaluator, rule_neg, _, message = self._make_setup(team)

        delta_run = EvaluationRunFactory.create(
            team=team,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.DELTA,
        )
        delta_result = EvaluationResultFactory.create(
            team=team,
            evaluator=evaluator,
            message=message,
            run=delta_run,
            output={"result": {"sentiment": "negative"}},
        )
        apply_rules_to_result(delta_result, evaluator, message)
        delta_run.scoped_messages.add(message)

        chat_message = message.expected_output_chat_message
        assert chat_message.tags.filter(pk=rule_neg.tag_id).exists()

        undo_run_tags(delta_run)

        # DELTA undo is a no-op: the tag remains and the run is not archived.
        assert chat_message.tags.filter(pk=rule_neg.tag_id).exists()
        delta_run.refresh_from_db()
        assert delta_run.tags_archived is False

    def test_equal_finished_at_uses_id_tiebreaker_and_strict_bound(self, team):
        """Two FULL runs finishing at the same instant: the higher-id run is the 'latest'
        (``-finished_at, -id`` order) and is the only one undoable, and the restore-set
        lookup uses a strict ``finished_at`` bound — so the equal-time earlier run is NOT
        treated as the prior epoch and undo strips to untagged rather than reverting to it.
        """
        config, evaluator, rule_neg, rule_pos, message = self._make_setup(team)
        same_time = timezone.now()

        full_a = self._make_run(config, {"result": {"sentiment": "negative"}}, rule_neg, message)
        full_b = self._make_run(config, {"result": {"sentiment": "positive"}}, rule_pos, message)
        # Force identical finished_at; full_b keeps the higher id (created later).
        EvaluationRun.objects.filter(pk__in=[full_a.pk, full_b.pk]).update(finished_at=same_time)
        full_a.refresh_from_db()
        full_b.refresh_from_db()

        # The -id tiebreaker makes full_b the latest; only it is undoable.
        assert _latest_completed_full_run(config).pk == full_b.pk
        assert can_undo_tags(full_b) is True
        assert can_undo_tags(full_a) is False

        # Strict bound: full_a (equal finished_at) is not "before" full_b, so nothing is restored.
        assert _restore_set_for_run(full_b) == []

        # Simulate reverse_stale_tags removing the superseded "bad" tag full_a applied.
        chat_message = message.expected_output_chat_message
        CustomTaggedItem.objects.filter(object_id=chat_message.pk, tag_id=rule_neg.tag_id).delete()
        assert chat_message.tags.filter(pk=rule_pos.tag_id).exists()

        undo_run_tags(full_b)

        # No restorable prior epoch -> full_b's tags are stripped, nothing comes back.
        assert not chat_message.tags.filter(pk=rule_pos.tag_id).exists()
        assert not chat_message.tags.filter(pk=rule_neg.tag_id).exists()

    def test_session_mode_restores_chat_tags(self, team):
        """Session-mode runs tag the Chat object; undo must restore those tags."""
        config, evaluator, rule_neg, _, message = self._make_setup(team, mode=EvaluationMode.SESSION)
        run = self._make_run(config, {"result": {"sentiment": "negative"}}, rule_neg, message)

        chat = message.session.chat
        assert chat.tags.filter(pk=rule_neg.tag_id).exists()

        undo_run_tags(run)

        # No previous run -> stripped
        assert not chat.tags.filter(pk=rule_neg.tag_id).exists()

    def test_undo_restores_full_epoch_across_intervening_delta(self, team):
        """Undoing a FULL run restores the previous FULL plus the DELTAs between them.

        Sequence:
          Run A (FULL):  applies "bad" to message M  (output sentiment=negative)
          Run B (DELTA): scoped to OTHER message, does NOT evaluate M
          Run C (FULL):  re-evaluates M with sentiment=positive -> applies "good";
                         reverse_stale_tags would have removed "bad" from M.

        Undo of Run C restores the Run A + Run B epoch: M reverts to Run A's "bad"
        (Run B only tagged OTHER, so the restore set's tag for M comes from Run A).
        """
        config, evaluator, rule_neg, rule_pos, message = self._make_setup(team)

        other_message = EvaluationMessageFactory.create(create_chat_messages=True)
        config.dataset.messages.add(other_message)

        # Run A (FULL): applies "bad" to M
        run_a = self._make_run(config, {"result": {"sentiment": "negative"}}, rule_neg, message)
        _stamp(run_a, timedelta(hours=-3))

        # Run B (DELTA): evaluates only other_message, NOT M
        run_b = EvaluationRunFactory.create(
            team=team,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.DELTA,
        )
        other_result = EvaluationResultFactory.create(
            team=team,
            evaluator=evaluator,
            message=other_message,
            run=run_b,
            output={"result": {"sentiment": "positive"}},
        )
        apply_rules_to_result(other_result, evaluator, other_message)
        run_b.scoped_messages.add(other_message)
        _stamp(run_b, timedelta(hours=-2))

        # Run C (FULL): applies "good" to M; simulate reverse_stale_tags stripping "bad"
        run_c = self._make_run(config, {"result": {"sentiment": "positive"}}, rule_pos, message)
        chat_message = message.expected_output_chat_message
        CustomTaggedItem.objects.filter(object_id=chat_message.pk, tag_id=rule_neg.tag_id).delete()

        # Sanity: only "good" is on M now
        assert chat_message.tags.filter(pk=rule_pos.tag_id).exists()
        assert not chat_message.tags.filter(pk=rule_neg.tag_id).exists()

        undo_run_tags(run_c)

        # After undo: "bad" restored from Run A (the restore set is Run A + Run B);
        # "good" removed.
        assert chat_message.tags.filter(pk=rule_neg.tag_id).exists(), (
            "Undo should have restored Run A's tag on M from the prior epoch."
        )
        assert not chat_message.tags.filter(pk=rule_pos.tag_id).exists()

    def test_undo_does_not_restore_tags_for_messages_skipped_by_this_run(self, team):
        """Walk is anchored to THIS run's EvaluationResults, not the live dataset.

        A message in the dataset that this run did not evaluate must be left
        untouched by undo, even if a prior run had applied a managed tag to it.
        """
        config, evaluator, rule_neg, _, message = self._make_setup(team)

        # other_message has a prior tag from a FULL run, but the current DELTA run won't touch it
        other_message = EvaluationMessageFactory.create(create_chat_messages=True)
        config.dataset.messages.add(other_message)

        prior_run = EvaluationRunFactory.create(
            team=team,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.FULL,
        )
        prior_result = EvaluationResultFactory.create(
            team=team,
            evaluator=evaluator,
            message=other_message,
            run=prior_run,
            output={"result": {"sentiment": "negative"}},
        )
        apply_rules_to_result(prior_result, evaluator, other_message)
        _stamp(prior_run, timedelta(hours=-2))

        # Current FULL run only evaluates `message`, not `other_message`
        current_run = EvaluationRunFactory.create(
            team=team,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.FULL,
        )
        current_result = EvaluationResultFactory.create(
            team=team,
            evaluator=evaluator,
            message=message,
            run=current_run,
            output={"result": {"sentiment": "negative"}},
        )
        apply_rules_to_result(current_result, evaluator, message)
        _stamp(current_run, timedelta(hours=-1))

        other_chat_message = other_message.expected_output_chat_message
        assert other_chat_message.tags.filter(pk=rule_neg.tag_id).exists()

        undo_run_tags(current_run)

        # other_message was not evaluated by current_run -> its tag must be untouched
        assert other_chat_message.tags.filter(pk=rule_neg.tag_id).exists()
