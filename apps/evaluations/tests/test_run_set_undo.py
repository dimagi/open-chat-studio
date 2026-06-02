"""Integration tests for the run-set undo model (FULL-only undo over tagging epochs).

These exercise the realistic task flow — apply_rules_to_result + reverse_stale_tags +
archive_superseded_runs per run, mirroring mark_evaluation_complete — across sequences
of FULL and DELTA runs, then undo the latest FULL run and assert the live tags revert to
the previous FULL plus the DELTAs that ran between it and the undone run.
"""

from collections import namedtuple
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
    reverse_stale_tags,
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

NEG = {"result": {"sentiment": "negative"}}
POS = {"result": {"sentiment": "positive"}}

# Bundles the config + evaluator a run needs so _complete_run stays within a small arg count.
Setup = namedtuple("Setup", "config evaluator rule_neg rule_pos")


@pytest.fixture()
def team(db):
    return TeamFactory.create()


@pytest.fixture()
def base_time():
    return timezone.now()


def _setup(team) -> Setup:
    """Config with two opposite-sentiment rules ('bad'/'good') and an empty dataset."""
    evaluator = EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.MESSAGE)
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
    dataset = EvaluationDatasetFactory.create(team=team, messages=[])
    config = EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[evaluator])
    return Setup(config, evaluator, rule_neg, rule_pos)


def _complete_run(setup: Setup, results, run_type, finished_at):
    """Create + finish a run that evaluated `results` (list of (message, output)).

    Mirrors mark_evaluation_complete: applies tag rules per result, then runs
    reverse_stale_tags and archive_superseded_runs. `finished_at` stamps the run so
    runs order deterministically.
    """
    run = EvaluationRunFactory.create(
        team=setup.config.team, config=setup.config, status=EvaluationRunStatus.COMPLETED, type=run_type
    )
    EvaluationRun.objects.filter(pk=run.pk).update(created_at=finished_at, finished_at=finished_at)
    run.refresh_from_db()

    scoped = []
    for message, output in results:
        result = EvaluationResultFactory.create(
            team=setup.config.team, evaluator=setup.evaluator, message=message, run=run, output=output
        )
        apply_rules_to_result(result, setup.evaluator, message)
        scoped.append(message)
    if run_type == EvaluationRunType.DELTA:
        run.scoped_messages.add(*scoped)

    reverse_stale_tags(run)
    archive_superseded_runs(run)
    return run


def _tags(message):
    return set(message.expected_output_chat_message.tags.values_list("name", flat=True))


def _new_dataset_message(config):
    """Create a chat-backed message and add it to the config's dataset."""
    message = EvaluationMessageFactory.create(create_chat_messages=True)
    config.dataset.messages.add(message)
    return message


class TestRunSetUndo:
    def test_undo_full_restores_previous_full_and_intervening_deltas(self, team, base_time):
        """FULL1 -> DELTA1 -> DELTA2 -> FULL2; undo FULL2 reverts every session to the
        run that last tagged it before FULL2: m_orig->FULL1, m_d1->DELTA1, m_d2->DELTA2."""
        setup = _setup(team)

        m_orig = _new_dataset_message(setup.config)
        full1 = _complete_run(setup, [(m_orig, NEG)], EvaluationRunType.FULL, base_time)

        m_d1 = _new_dataset_message(setup.config)
        delta1 = _complete_run(setup, [(m_d1, NEG)], EvaluationRunType.DELTA, base_time + timedelta(minutes=10))

        m_d2 = _new_dataset_message(setup.config)
        delta2 = _complete_run(setup, [(m_d2, NEG)], EvaluationRunType.DELTA, base_time + timedelta(minutes=20))

        # FULL2 re-tags everything positive ("good"), superseding all three.
        full2 = _complete_run(
            setup,
            [(m_orig, POS), (m_d1, POS), (m_d2, POS)],
            EvaluationRunType.FULL,
            base_time + timedelta(minutes=30),
        )

        # Live state after FULL2: all "good".
        assert _tags(m_orig) == {"good"}
        assert _tags(m_d1) == {"good"}
        assert _tags(m_d2) == {"good"}

        assert can_undo_tags(full2) is True
        undo_run_tags(full2)

        # Reverts: m_orig from FULL1, m_d1 from DELTA1, m_d2 from DELTA2 — all "bad".
        assert _tags(m_orig) == {"bad"}
        assert _tags(m_d1) == {"bad"}
        assert _tags(m_d2) == {"bad"}

        # Flags: FULL2 archived; the restored epoch active again.
        for run in (full1, delta1, delta2):
            run.refresh_from_db()
            assert run.tags_archived is False
        full2.refresh_from_db()
        assert full2.tags_archived is True

        # Undo is spent: neither FULL is undoable now.
        assert can_undo_tags(full2) is False
        assert can_undo_tags(full1) is False

    def test_undo_restores_only_the_latest_epoch(self, team, base_time):
        """FULL1 -> DELTA1 -> FULL2 -> DELTA2 -> FULL3; undo FULL3 restores FULL2 + DELTA2
        only. FULL1 and DELTA1 (superseded by FULL2) stay archived."""
        setup = _setup(team)

        m_orig = _new_dataset_message(setup.config)
        full1 = _complete_run(setup, [(m_orig, NEG)], EvaluationRunType.FULL, base_time)

        m_d1 = _new_dataset_message(setup.config)
        delta1 = _complete_run(setup, [(m_d1, NEG)], EvaluationRunType.DELTA, base_time + timedelta(minutes=10))

        # FULL2: m_orig stays bad, m_d1 flips to good.
        full2 = _complete_run(
            setup, [(m_orig, NEG), (m_d1, POS)], EvaluationRunType.FULL, base_time + timedelta(minutes=20)
        )

        m_d2 = _new_dataset_message(setup.config)
        delta2 = _complete_run(setup, [(m_d2, NEG)], EvaluationRunType.DELTA, base_time + timedelta(minutes=30))

        # FULL3: everything good.
        full3 = _complete_run(
            setup, [(m_orig, POS), (m_d1, POS), (m_d2, POS)], EvaluationRunType.FULL, base_time + timedelta(minutes=40)
        )
        assert _tags(m_orig) == {"good"}
        assert _tags(m_d1) == {"good"}
        assert _tags(m_d2) == {"good"}

        undo_run_tags(full3)

        # Restored to the FULL2 epoch: m_orig bad (FULL2), m_d1 good (FULL2), m_d2 bad (DELTA2).
        assert _tags(m_orig) == {"bad"}
        assert _tags(m_d1) == {"good"}
        assert _tags(m_d2) == {"bad"}

        full2.refresh_from_db()
        delta2.refresh_from_db()
        assert full2.tags_archived is False
        assert delta2.tags_archived is False
        # The older epoch stays archived.
        full1.refresh_from_db()
        delta1.refresh_from_db()
        assert full1.tags_archived is True
        assert delta1.tags_archived is True

    def test_delta_completion_archives_nothing(self, team, base_time):
        """A DELTA run only adds disjoint new-session tags; it supersedes no prior run."""
        setup = _setup(team)

        m_orig = _new_dataset_message(setup.config)
        full1 = _complete_run(setup, [(m_orig, NEG)], EvaluationRunType.FULL, base_time)

        m_d1 = _new_dataset_message(setup.config)
        delta1 = _complete_run(setup, [(m_d1, NEG)], EvaluationRunType.DELTA, base_time + timedelta(minutes=10))

        full1.refresh_from_db()
        delta1.refresh_from_db()
        assert full1.tags_archived is False
        assert delta1.tags_archived is False
        assert _tags(m_orig) == {"bad"}
        assert _tags(m_d1) == {"bad"}

    def test_full_completion_archives_prior_full_and_deltas(self, team, base_time):
        setup = _setup(team)

        m_orig = _new_dataset_message(setup.config)
        full1 = _complete_run(setup, [(m_orig, NEG)], EvaluationRunType.FULL, base_time)

        m_d1 = _new_dataset_message(setup.config)
        delta1 = _complete_run(setup, [(m_d1, NEG)], EvaluationRunType.DELTA, base_time + timedelta(minutes=10))

        full2 = _complete_run(
            setup, [(m_orig, POS), (m_d1, POS)], EvaluationRunType.FULL, base_time + timedelta(minutes=20)
        )

        for run in (full1, delta1):
            run.refresh_from_db()
            assert run.tags_archived is True
        full2.refresh_from_db()
        assert full2.tags_archived is False
