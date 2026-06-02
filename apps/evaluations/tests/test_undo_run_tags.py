"""Tests for the run-level tag-undo model: archive-on-supersede, undo, and eligibility.

Covers the full undo/archive surface in one place:

* ``archive_superseded_runs`` / ``can_undo_tags`` — the ``tags_archived`` bookkeeping.
* ``undo_run_tags`` — reverting a FULL run's live tags to the prior epoch, including
  session-mode targets, intervening DELTAs, and the equal-``finished_at`` tiebreaker.
* ``_compute_undo_target_diffs`` — the per-target remove/add diff that drives undo.
"""

from collections import namedtuple
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
    _compute_undo_target_diffs,
    _latest_completed_full_run,
    _restore_set_for_run,
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
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory

NEG = {"result": {"sentiment": "negative"}}  # fires rule_neg -> "bad"
POS = {"result": {"sentiment": "positive"}}  # fires rule_pos -> "good"

# Bundles the fixtures a test needs so helpers stay within a small argument count.
Setup = namedtuple("Setup", "config evaluator rule_neg rule_pos message")


@pytest.fixture()
def team(db):
    return TeamFactory.create()


def _stamp(run, delta, finished_delta=None):
    """Force created_at (and finished_at) on a run via QuerySet.update() (bypasses auto_now_add).

    ``created_at`` is set to now() + ``delta``. ``finished_at`` is set to now() + ``finished_delta``,
    defaulting to ``delta`` so the two move together unless a test deliberately models an
    overlapping run that finished at a different time than it was created.
    """
    now = timezone.now()
    if finished_delta is None:
        finished_delta = delta
    EvaluationRun.objects.filter(pk=run.pk).update(created_at=now + delta, finished_at=now + finished_delta)
    run.refresh_from_db()


def _make_setup(team, mode=EvaluationMode.MESSAGE) -> Setup:
    """Build a config with two opposite-sentiment rules ('bad'/'good') and one dataset message.

    Both rules target the same ``sentiment`` field with opposite values, so a NEG/POS
    output flips cleanly between the "bad" and "good" tags.
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
    return Setup(config, evaluator, rule_neg, rule_pos, message)


def _make_run(setup: Setup, output, run_type=EvaluationRunType.FULL, message=None):
    """Create a COMPLETED run that evaluated ``message`` (defaults to the setup's message),
    apply the matching tag rule, and return the run."""
    message = message or setup.message
    run = EvaluationRunFactory.create(
        team=setup.config.team,
        config=setup.config,
        status=EvaluationRunStatus.COMPLETED,
        type=run_type,
        finished_at=timezone.now(),
    )
    result = EvaluationResultFactory.create(
        team=setup.config.team, evaluator=setup.evaluator, message=message, run=run, output=output
    )
    apply_rules_to_result(result, setup.evaluator, message)
    return run


class TestArchiveSupersededRuns:
    def test_prior_run_archived_when_new_full_run_completes(self, team):
        setup = _make_setup(team)
        run1 = _make_run(setup, NEG)
        _stamp(run1, timedelta(hours=-1))
        run2 = _make_run(setup, POS)

        archive_superseded_runs(run2)

        run1.refresh_from_db()
        run2.refresh_from_db()
        assert run1.tags_archived is True
        assert run2.tags_archived is False

    def test_preview_run_is_no_op(self, team):
        setup = _make_setup(team)
        run1 = _make_run(setup, NEG)
        _stamp(run1, timedelta(hours=-1))
        preview = _make_run(setup, POS, run_type=EvaluationRunType.PREVIEW)

        archive_superseded_runs(preview)

        run1.refresh_from_db()
        assert run1.tags_archived is False  # preview never supersedes

    def test_delta_run_archives_nothing(self, team):
        """A DELTA run only adds disjoint new-session tags; it supersedes no prior run."""
        setup = _make_setup(team)
        run1 = _make_run(setup, NEG)
        _stamp(run1, timedelta(hours=-1))

        other_message = EvaluationMessageFactory.create(create_chat_messages=True)
        setup.config.dataset.messages.add(other_message)
        delta = _make_run(setup, NEG, run_type=EvaluationRunType.DELTA, message=other_message)
        delta.scoped_messages.add(other_message)

        archive_superseded_runs(delta)

        run1.refresh_from_db()
        delta.refresh_from_db()
        assert run1.tags_archived is False
        assert delta.tags_archived is False

    def test_full_run_archives_prior_full_and_its_deltas(self, team):
        setup = _make_setup(team)
        full1 = _make_run(setup, NEG)
        _stamp(full1, timedelta(hours=-2))

        other_message = EvaluationMessageFactory.create(create_chat_messages=True)
        setup.config.dataset.messages.add(other_message)
        delta = _make_run(setup, NEG, run_type=EvaluationRunType.DELTA, message=other_message)
        delta.scoped_messages.add(other_message)
        _stamp(delta, timedelta(hours=-1))

        full2 = _make_run(setup, POS)
        archive_superseded_runs(full2)

        full1.refresh_from_db()
        delta.refresh_from_db()
        full2.refresh_from_db()
        assert full1.tags_archived is True
        assert delta.tags_archived is True
        assert full2.tags_archived is False


class TestUndoArchiving:
    def test_undo_archives_current_and_reactivates_prior(self, team):
        setup = _make_setup(team)
        run1 = _make_run(setup, NEG)
        _stamp(run1, timedelta(hours=-1))
        run2 = _make_run(setup, POS)
        archive_superseded_runs(run2)  # run1 -> archived, run2 -> active

        undo_run_tags(run2)

        run1.refresh_from_db()
        run2.refresh_from_db()
        assert run2.tags_archived is True
        assert run1.tags_archived is False

    def test_undo_with_no_prior_run_archives_current(self, team):
        setup = _make_setup(team)
        run1 = _make_run(setup, NEG)

        undo_run_tags(run1)

        run1.refresh_from_db()
        assert run1.tags_archived is True


class TestCanUndoTags:
    def test_latest_full_run_is_undoable(self, team):
        setup = _make_setup(team)
        run = _make_run(setup, NEG)
        assert can_undo_tags(run) is True

    def test_non_latest_full_run_not_undoable(self, team):
        setup = _make_setup(team)
        run1 = _make_run(setup, NEG)
        _stamp(run1, timedelta(hours=-1))
        _make_run(setup, POS)
        assert can_undo_tags(run1) is False

    def test_delta_run_not_undoable(self, team):
        setup = _make_setup(team)
        delta = _make_run(setup, NEG, run_type=EvaluationRunType.DELTA)
        assert can_undo_tags(delta) is False

    def test_preview_run_not_undoable(self, team):
        setup = _make_setup(team)
        preview = _make_run(setup, NEG, run_type=EvaluationRunType.PREVIEW)
        assert can_undo_tags(preview) is False

    def test_already_undone_run_not_undoable(self, team):
        setup = _make_setup(team)
        run1 = _make_run(setup, NEG)
        _stamp(run1, timedelta(hours=-1))
        run2 = _make_run(setup, POS)
        archive_superseded_runs(run2)

        assert can_undo_tags(run2) is True
        undo_run_tags(run2)
        run1.refresh_from_db()
        run2.refresh_from_db()
        # After undo: run2 is latest-but-archived, run1 is active-but-not-latest -> neither undoable
        assert can_undo_tags(run2) is False
        assert can_undo_tags(run1) is False

    def test_processing_run_not_undoable(self, team):
        setup = _make_setup(team)
        run = EvaluationRunFactory.create(
            team=team, config=setup.config, status=EvaluationRunStatus.PROCESSING, type=EvaluationRunType.FULL
        )
        assert can_undo_tags(run) is False


class TestUndoRunTags:
    def test_preview_run_is_no_op(self, team):
        """PREVIEW runs must never be touched by undo."""
        setup = _make_setup(team)
        run = _make_run(setup, NEG, run_type=EvaluationRunType.PREVIEW)

        chat_message = setup.message.expected_output_chat_message
        assert chat_message.tags.filter(pk=setup.rule_neg.tag_id).exists()

        undo_run_tags(run)

        # Still has the tag; PREVIEW was a no-op
        assert chat_message.tags.filter(pk=setup.rule_neg.tag_id).exists()

    def test_no_previous_run_removes_current_tags(self, team):
        """With no prior run, undo simply strips the current run's applied tags."""
        setup = _make_setup(team)
        run = _make_run(setup, NEG)

        chat_message = setup.message.expected_output_chat_message
        assert chat_message.tags.filter(pk=setup.rule_neg.tag_id).exists()

        undo_run_tags(run)

        assert not chat_message.tags.filter(pk=setup.rule_neg.tag_id).exists()
        # AppliedTag audit records are NOT deleted
        assert AppliedTag.objects.filter(evaluation_result__run=run).exists()

    def test_restores_previous_run_tags_and_removes_current(self, team):
        """
        Simulate two successive runs where the tag switches:
          prev run  -> "bad" (negative) applied
          curr run  -> "good" (positive) applied; reverse_stale_tags would have removed "bad"

        After undo: "bad" is back, "good" is gone.
        """
        setup = _make_setup(team)

        # Build previous run (applied "bad")
        prev_run = _make_run(setup, NEG)
        _stamp(prev_run, timedelta(hours=-1))

        # Build current run (applied "good")
        # No _stamp needed: prev_run is at now()-1h, so curr_run's natural created_at is later.
        curr_run = _make_run(setup, POS)

        # Simulate what reverse_stale_tags would have done: remove "bad" from target
        chat_message = setup.message.expected_output_chat_message
        CustomTaggedItem.objects.filter(object_id=chat_message.pk, tag_id=setup.rule_neg.tag_id).delete()

        # Precondition: only "good" is currently on the message
        assert chat_message.tags.filter(pk=setup.rule_pos.tag_id).exists()
        assert not chat_message.tags.filter(pk=setup.rule_neg.tag_id).exists()

        undo_run_tags(curr_run)

        # After undo: "bad" restored, "good" removed
        assert chat_message.tags.filter(pk=setup.rule_neg.tag_id).exists()
        assert not chat_message.tags.filter(pk=setup.rule_pos.tag_id).exists()

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
        setup = _make_setup(team)
        delta_run = _make_run(setup, NEG, run_type=EvaluationRunType.DELTA)
        delta_run.scoped_messages.add(setup.message)

        chat_message = setup.message.expected_output_chat_message
        assert chat_message.tags.filter(pk=setup.rule_neg.tag_id).exists()

        undo_run_tags(delta_run)

        # DELTA undo is a no-op: the tag remains and the run is not archived.
        assert chat_message.tags.filter(pk=setup.rule_neg.tag_id).exists()
        delta_run.refresh_from_db()
        assert delta_run.tags_archived is False

    def test_equal_finished_at_uses_id_tiebreaker_and_strict_bound(self, team):
        """Two FULL runs finishing at the same instant: the higher-id run is the 'latest'
        (``-finished_at, -id`` order) and is the only one undoable, and the restore-set
        lookup uses a strict ``finished_at`` bound — so the equal-time earlier run is NOT
        treated as the prior epoch and undo strips to untagged rather than reverting to it.
        """
        setup = _make_setup(team)
        same_time = timezone.now()

        full_a = _make_run(setup, NEG)
        full_b = _make_run(setup, POS)
        # Force identical finished_at; full_b keeps the higher id (created later).
        EvaluationRun.objects.filter(pk__in=[full_a.pk, full_b.pk]).update(finished_at=same_time)
        full_a.refresh_from_db()
        full_b.refresh_from_db()

        # The -id tiebreaker makes full_b the latest; only it is undoable.
        assert _latest_completed_full_run(setup.config).pk == full_b.pk
        assert can_undo_tags(full_b) is True
        assert can_undo_tags(full_a) is False

        # Strict bound: full_a (equal finished_at) is not "before" full_b, so nothing is restored.
        assert _restore_set_for_run(full_b) == []

        # Simulate reverse_stale_tags removing the superseded "bad" tag full_a applied.
        chat_message = setup.message.expected_output_chat_message
        CustomTaggedItem.objects.filter(object_id=chat_message.pk, tag_id=setup.rule_neg.tag_id).delete()
        assert chat_message.tags.filter(pk=setup.rule_pos.tag_id).exists()

        undo_run_tags(full_b)

        # No restorable prior epoch -> full_b's tags are stripped, nothing comes back.
        assert not chat_message.tags.filter(pk=setup.rule_pos.tag_id).exists()
        assert not chat_message.tags.filter(pk=setup.rule_neg.tag_id).exists()

    def test_session_mode_restores_chat_tags(self, team):
        """Session-mode runs tag the Chat object; undo must restore those tags."""
        setup = _make_setup(team, mode=EvaluationMode.SESSION)
        run = _make_run(setup, NEG)

        chat = setup.message.session.chat
        assert chat.tags.filter(pk=setup.rule_neg.tag_id).exists()

        undo_run_tags(run)

        # No previous run -> stripped
        assert not chat.tags.filter(pk=setup.rule_neg.tag_id).exists()

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
        setup = _make_setup(team)

        other_message = EvaluationMessageFactory.create(create_chat_messages=True)
        setup.config.dataset.messages.add(other_message)

        # Run A (FULL): applies "bad" to M
        run_a = _make_run(setup, NEG)
        _stamp(run_a, timedelta(hours=-3))

        # Run B (DELTA): evaluates only other_message, NOT M
        run_b = _make_run(setup, POS, run_type=EvaluationRunType.DELTA, message=other_message)
        run_b.scoped_messages.add(other_message)
        _stamp(run_b, timedelta(hours=-2))

        # Run C (FULL): applies "good" to M; simulate reverse_stale_tags stripping "bad"
        run_c = _make_run(setup, POS)
        chat_message = setup.message.expected_output_chat_message
        CustomTaggedItem.objects.filter(object_id=chat_message.pk, tag_id=setup.rule_neg.tag_id).delete()

        # Sanity: only "good" is on M now
        assert chat_message.tags.filter(pk=setup.rule_pos.tag_id).exists()
        assert not chat_message.tags.filter(pk=setup.rule_neg.tag_id).exists()

        undo_run_tags(run_c)

        # After undo: "bad" restored from Run A (the restore set is Run A + Run B);
        # "good" removed.
        assert chat_message.tags.filter(pk=setup.rule_neg.tag_id).exists(), (
            "Undo should have restored Run A's tag on M from the prior epoch."
        )
        assert not chat_message.tags.filter(pk=setup.rule_pos.tag_id).exists()

    def test_undo_does_not_restore_tags_for_messages_skipped_by_this_run(self, team):
        """Walk is anchored to THIS run's EvaluationResults, not the live dataset.

        A message in the dataset that this run did not evaluate must be left
        untouched by undo, even if a prior run had applied a managed tag to it.
        """
        setup = _make_setup(team)

        # other_message has a prior tag from a FULL run, but the current run won't touch it
        other_message = EvaluationMessageFactory.create(create_chat_messages=True)
        setup.config.dataset.messages.add(other_message)

        prior_run = _make_run(setup, NEG, message=other_message)
        _stamp(prior_run, timedelta(hours=-2))

        # Current FULL run only evaluates `message`, not `other_message`
        current_run = _make_run(setup, NEG)
        _stamp(current_run, timedelta(hours=-1))

        other_chat_message = other_message.expected_output_chat_message
        assert other_chat_message.tags.filter(pk=setup.rule_neg.tag_id).exists()

        undo_run_tags(current_run)

        # other_message was not evaluated by current_run -> its tag must be untouched
        assert other_chat_message.tags.filter(pk=setup.rule_neg.tag_id).exists()


class TestComputeUndoTargetDiffs:
    def test_diffs_remove_current_and_add_restored_tags(self, team):
        """The diff removes tags this run added that the restore set lacks, and adds
        restore-set tags this run dropped — both bounded to ``possible_tags``."""
        setup = _make_setup(team)

        prior = _make_run(setup, NEG)  # restore epoch applied "bad"
        _stamp(prior, timedelta(hours=-1))
        current = _make_run(setup, POS)  # current run applied "good"

        possible_tags = frozenset({setup.rule_neg.tag_id, setup.rule_pos.tag_id})
        remove_by_target, add_by_target = _compute_undo_target_diffs(
            current, [prior], possible_tags, EvaluationMode.MESSAGE
        )

        target_pk = setup.message.expected_output_chat_message.pk
        assert remove_by_target[target_pk] == {setup.rule_pos.tag_id}  # "good" added by current, not in restore
        assert add_by_target[target_pk] == {setup.rule_neg.tag_id}  # "bad" in restore, dropped by current

    def test_common_tags_are_left_untouched(self, team):
        """A tag present in both the current run and the restore set produces no diff."""
        setup = _make_setup(team)

        prior = _make_run(setup, NEG)  # "bad"
        _stamp(prior, timedelta(hours=-1))
        current = _make_run(setup, NEG)  # "bad" again — unchanged

        possible_tags = frozenset({setup.rule_neg.tag_id, setup.rule_pos.tag_id})
        remove_by_target, add_by_target = _compute_undo_target_diffs(
            current, [prior], possible_tags, EvaluationMode.MESSAGE
        )

        target_pk = setup.message.expected_output_chat_message.pk
        assert remove_by_target[target_pk] == set()
        assert add_by_target[target_pk] == set()
