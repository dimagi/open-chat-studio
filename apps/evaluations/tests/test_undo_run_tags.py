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
from apps.evaluations.tagging import _message_ids_by_latest_prior_run, apply_rules_to_result, undo_run_tags
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

    def test_delta_run_only_processes_scoped_messages(self, team):
        """DELTA runs scope to scoped_messages; undo only touches those targets."""
        config, evaluator, rule_neg, _, message = self._make_setup(team)
        # Add a second message NOT in the delta scope
        other_message = EvaluationMessageFactory.create(create_chat_messages=True)
        config.dataset.messages.add(other_message)

        # Apply the tag to other_message directly (simulating a previous run's effect)
        # We'll use apply_rules_to_result with a separate run so it has AppliedTag rows
        setup_run = EvaluationRunFactory.create(
            team=team,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.FULL,
        )
        other_result = EvaluationResultFactory.create(
            team=team,
            evaluator=evaluator,
            message=other_message,
            run=setup_run,
            output={"result": {"sentiment": "negative"}},
        )
        apply_rules_to_result(other_result, evaluator, other_message)
        _stamp(setup_run, timedelta(hours=-2))

        # DELTA run scoped only to `message` (not `other_message`)
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
        _stamp(delta_run, timedelta(hours=-1))

        other_chat_message = other_message.expected_output_chat_message
        assert other_chat_message.tags.filter(pk=rule_neg.tag_id).exists()

        undo_run_tags(delta_run)

        # other_message was NOT in the delta scope — its tag must be untouched
        assert other_chat_message.tags.filter(pk=rule_neg.tag_id).exists()

    def test_session_mode_restores_chat_tags(self, team):
        """Session-mode runs tag the Chat object; undo must restore those tags."""
        config, evaluator, rule_neg, _, message = self._make_setup(team, mode=EvaluationMode.SESSION)
        run = self._make_run(config, {"result": {"sentiment": "negative"}}, rule_neg, message)

        chat = message.session.chat
        assert chat.tags.filter(pk=rule_neg.tag_id).exists()

        undo_run_tags(run)

        # No previous run -> stripped
        assert not chat.tags.filter(pk=rule_neg.tag_id).exists()

    def test_undo_walks_past_delta_that_skipped_message(self, team):
        """Undo must look past a DELTA predecessor that did not evaluate the target message.

        Sequence:
          Run A (FULL):  applies "bad" to message M  (output sentiment=negative)
          Run B (DELTA): scoped to OTHER message, does NOT evaluate M
          Run C (FULL):  re-evaluates M with sentiment=positive -> applies "good";
                         reverse_stale_tags would have removed "bad" from M.

        Undo of Run C must restore Run A's tag on M (not leave M tagless), because
        Run B did not evaluate M and so its "state for M" is older than A.
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

        # After undo: "bad" restored from Run A (NOT left tagless because B skipped M);
        # "good" removed.
        assert chat_message.tags.filter(pk=rule_neg.tag_id).exists(), (
            "Undo should have walked past the DELTA that skipped M and restored Run A's tag."
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


# ---- _message_ids_by_latest_prior_run ----------------------------------------


class TestMessageIdsByLatestPriorRun:
    """Focused tests for the candidates queryset that drives undo's previous-state lookup."""

    def _make_config(self, team):
        evaluator = EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.MESSAGE)
        dataset = EvaluationDatasetFactory.create(team=team, messages=[])
        return EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[evaluator]), evaluator

    def _make_run_with_results(self, config, evaluator, messages, run_type=EvaluationRunType.FULL):
        run = EvaluationRunFactory.create(
            team=config.team,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=run_type,
            finished_at=timezone.now(),
        )
        for message in messages:
            EvaluationResultFactory.create(
                team=config.team, evaluator=evaluator, message=message, run=run, output={"result": {}}
            )
        return run

    def test_empty_message_ids_returns_empty(self, team):
        config, _ = self._make_config(team)
        future_run = EvaluationRunFactory.create(
            team=team, config=config, status=EvaluationRunStatus.COMPLETED, finished_at=timezone.now()
        )
        assert _message_ids_by_latest_prior_run(future_run, []) == {}

    def test_returns_most_recent_prior_run_per_message(self, team):
        """When two prior runs evaluated the same message, only the latest is returned."""
        config, evaluator = self._make_config(team)
        message = EvaluationMessageFactory.create(create_chat_messages=True)

        old_run = self._make_run_with_results(config, evaluator, [message])
        _stamp(old_run, timedelta(hours=-3))

        recent_run = self._make_run_with_results(config, evaluator, [message])
        _stamp(recent_run, timedelta(hours=-1))

        # The current run has to be later than both
        curr_run = self._make_run_with_results(config, evaluator, [message])

        result = _message_ids_by_latest_prior_run(curr_run, [message.pk])
        assert dict(result) == {recent_run.pk: {message.pk}}

    def test_groups_messages_sharing_a_previous_run(self, team):
        """Two messages whose latest prior run is the same should be grouped under that run_id."""
        config, evaluator = self._make_config(team)
        m1 = EvaluationMessageFactory.create(create_chat_messages=True)
        m2 = EvaluationMessageFactory.create(create_chat_messages=True)

        shared_prior = self._make_run_with_results(config, evaluator, [m1, m2])
        _stamp(shared_prior, timedelta(hours=-1))

        curr_run = self._make_run_with_results(config, evaluator, [m1, m2])

        result = _message_ids_by_latest_prior_run(curr_run, [m1.pk, m2.pk])
        assert dict(result) == {shared_prior.pk: {m1.pk, m2.pk}}

    def test_walks_past_delta_that_skipped_message(self, team):
        """A DELTA predecessor that did not touch a message must be skipped per-message."""
        config, evaluator = self._make_config(team)
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        other_message = EvaluationMessageFactory.create(create_chat_messages=True)

        # Run A (FULL): evaluated `message`
        run_a = self._make_run_with_results(config, evaluator, [message])
        _stamp(run_a, timedelta(hours=-3))

        # Run B (DELTA): evaluated only `other_message`
        self._make_run_with_results(config, evaluator, [other_message], run_type=EvaluationRunType.DELTA)

        # Current run
        curr_run = self._make_run_with_results(config, evaluator, [message])

        result = _message_ids_by_latest_prior_run(curr_run, [message.pk])
        # For `message`, the latest prior run is Run A — Run B skipped it.
        assert dict(result) == {run_a.pk: {message.pk}}

    def test_excludes_preview_and_non_completed_runs(self, team):
        """PREVIEW runs and non-COMPLETED runs must never appear as previous-state."""
        config, evaluator = self._make_config(team)
        message = EvaluationMessageFactory.create(create_chat_messages=True)

        preview_run = self._make_run_with_results(config, evaluator, [message], run_type=EvaluationRunType.PREVIEW)
        _stamp(preview_run, timedelta(hours=-2))

        # A pending run evaluated the message but is not COMPLETED
        pending_run = EvaluationRunFactory.create(
            team=team, config=config, status=EvaluationRunStatus.PENDING, type=EvaluationRunType.FULL
        )
        EvaluationResultFactory.create(
            team=team, evaluator=evaluator, message=message, run=pending_run, output={"result": {}}
        )
        _stamp(pending_run, timedelta(hours=-2))

        curr_run = self._make_run_with_results(config, evaluator, [message])

        assert _message_ids_by_latest_prior_run(curr_run, [message.pk]) == {}

    def test_excludes_runs_from_other_configs(self, team):
        """Only prior runs of the *same* config count as previous state."""
        config_a, evaluator_a = self._make_config(team)
        config_b, evaluator_b = self._make_config(team)
        message = EvaluationMessageFactory.create(create_chat_messages=True)

        # Prior run on config B that happens to have evaluated the same message
        other_config_run = self._make_run_with_results(config_b, evaluator_b, [message])
        _stamp(other_config_run, timedelta(hours=-2))

        # Current run on config A
        curr_run = self._make_run_with_results(config_a, evaluator_a, [message])

        assert _message_ids_by_latest_prior_run(curr_run, [message.pk]) == {}

    def test_excludes_runs_at_or_after_current(self, team):
        """Only runs strictly older than the current run are candidates."""
        config, evaluator = self._make_config(team)
        message = EvaluationMessageFactory.create(create_chat_messages=True)

        curr_run = self._make_run_with_results(config, evaluator, [message])

        # Run created after curr_run is not a candidate
        later_run = self._make_run_with_results(config, evaluator, [message])
        _stamp(later_run, timedelta(hours=1))

        assert _message_ids_by_latest_prior_run(curr_run, [message.pk]) == {}

    def test_only_returns_rows_for_requested_messages(self, team):
        """Messages outside `message_ids` must not appear, even if they have prior runs."""
        config, evaluator = self._make_config(team)
        m1 = EvaluationMessageFactory.create(create_chat_messages=True)
        m2 = EvaluationMessageFactory.create(create_chat_messages=True)

        prior_run = self._make_run_with_results(config, evaluator, [m1, m2])
        _stamp(prior_run, timedelta(hours=-1))

        curr_run = self._make_run_with_results(config, evaluator, [m1, m2])

        result = _message_ids_by_latest_prior_run(curr_run, [m1.pk])
        assert dict(result) == {prior_run.pk: {m1.pk}}

    def test_orders_by_finished_at_not_created_at(self, team):
        """When runs overlap, the run that *finished* most recently wins, even if it was
        created earlier. Runs can overlap (no concurrency guard), so completion time — not
        creation time — reflects which prior tag state is the most recent."""
        config, evaluator = self._make_config(team)
        message = EvaluationMessageFactory.create(create_chat_messages=True)

        # long_run: created earliest (-3h) but finished latest (-1h) — it overlapped.
        long_run = self._make_run_with_results(config, evaluator, [message])
        _stamp(long_run, timedelta(hours=-3), finished_delta=timedelta(hours=-1))

        # short_run: created later (-2h) but finished earlier (-90min).
        short_run = self._make_run_with_results(config, evaluator, [message])
        _stamp(short_run, timedelta(hours=-2), finished_delta=timedelta(minutes=-90))

        curr_run = self._make_run_with_results(config, evaluator, [message])

        # By finished_at, long_run (-1h) is the most recent prior state, not short_run.
        result = _message_ids_by_latest_prior_run(curr_run, [message.pk])
        assert dict(result) == {long_run.pk: {message.pk}}

    def test_tie_broken_deterministically_by_run_id(self, team):
        """Two prior runs finishing at the exact same instant must resolve deterministically:
        the higher run_id wins (stable tie-breaker for DISTINCT ON)."""
        config, evaluator = self._make_config(team)
        message = EvaluationMessageFactory.create(create_chat_messages=True)

        first = self._make_run_with_results(config, evaluator, [message])
        second = self._make_run_with_results(config, evaluator, [message])
        # Force identical created_at AND finished_at on both, so only the run_id tie-breaker
        # can separate them — otherwise DISTINCT ON picks an arbitrary row.
        tied = timezone.now() - timedelta(hours=1)
        EvaluationRun.objects.filter(pk__in=[first.pk, second.pk]).update(created_at=tied, finished_at=tied)
        first.refresh_from_db()
        second.refresh_from_db()

        curr_run = self._make_run_with_results(config, evaluator, [message])

        winner = max(first.pk, second.pk)
        result = _message_ids_by_latest_prior_run(curr_run, [message.pk])
        assert dict(result) == {winner: {message.pk}}
