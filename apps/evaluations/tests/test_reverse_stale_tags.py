"""Tests for reverse_stale_tags — stale eval-driven tag cleanup after a run."""

import pytest

from apps.annotations.models import CustomTaggedItem
from apps.evaluations.models import (
    ConditionType,
    EvaluationMode,
    EvaluationRunType,
)
from apps.evaluations.tagging import reverse_stale_tags
from apps.utils.factories.evaluations import (
    AppliedTagFactory,
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluationTagFactory,
    EvaluatorFactory,
    EvaluatorTagRuleFactory,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory


@pytest.fixture()
def team(db):
    return TeamFactory.create()


@pytest.fixture()
def evaluator(team):
    return EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.MESSAGE)


@pytest.fixture()
def tag_a(team):
    return EvaluationTagFactory.create(team=team, name="tag-a")


@pytest.fixture()
def tag_rule(team, evaluator, tag_a):
    return EvaluatorTagRuleFactory.create(
        team=team,
        evaluator=evaluator,
        tag=tag_a,
        field_name="sentiment",
        condition_type=ConditionType.EQUALS,
        condition_value={"value": "negative"},
    )


def _build_run(team, evaluator, messages, run_type=EvaluationRunType.FULL):
    dataset = EvaluationDatasetFactory.create(team=team, messages=messages)
    config = EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[evaluator])
    return EvaluationRunFactory.create(team=team, config=config, type=run_type)


class TestReverseStaleTags:
    def test_stale_tag_removed_from_target(self, team, evaluator, tag_a, tag_rule):
        """A tag in possible_tags but not applied in this run is removed from the target."""
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message

        chat_message.tags.add(tag_a, through_defaults={"team": team})
        assert chat_message.tags.filter(pk=tag_a.pk).exists()

        run = _build_run(team, evaluator, [message])
        EvaluationResultFactory.create(team=team, evaluator=evaluator, message=message, run=run, output={})

        reverse_stale_tags(run)

        assert not chat_message.tags.filter(pk=tag_a.pk).exists()

    def test_applied_tag_kept_on_target(self, team, evaluator, tag_a, tag_rule):
        """A tag that was applied in this run is not removed."""
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message

        chat_message.tags.add(tag_a, through_defaults={"team": team})

        run = _build_run(team, evaluator, [message])
        result = EvaluationResultFactory.create(team=team, evaluator=evaluator, message=message, run=run, output={})
        AppliedTagFactory.create(team=team, evaluation_result=result, rule=tag_rule, tag=tag_a)

        reverse_stale_tags(run)

        assert chat_message.tags.filter(pk=tag_a.pk).exists()

    def test_tag_outside_possible_tags_untouched(self, team, evaluator, tag_rule):
        """Tags not managed by any evaluator in the run are never removed."""
        tag_b = EvaluationTagFactory.create(team=team, name="tag-b-unmanaged")
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message

        chat_message.tags.add(tag_b, through_defaults={"team": team})

        run = _build_run(team, evaluator, [message])
        EvaluationResultFactory.create(team=team, evaluator=evaluator, message=message, run=run, output={})

        reverse_stale_tags(run)

        assert chat_message.tags.filter(pk=tag_b.pk).exists()

    def test_none_target_skipped_gracefully(self, team, evaluator, tag_rule):
        """CSV-imported messages with no chat message target are skipped without error."""
        message = EvaluationMessageFactory.create()  # no create_chat_messages → target is None

        run = _build_run(team, evaluator, [message])
        EvaluationResultFactory.create(team=team, evaluator=evaluator, message=message, run=run, output={})

        reverse_stale_tags(run)  # must not raise

        assert CustomTaggedItem.objects.count() == 0

    def test_delta_run_only_cleans_scoped_messages(self, team, evaluator, tag_a, tag_rule):
        """Delta run cleanup is limited to scoped_messages; out-of-scope messages are untouched."""
        msg_scoped = EvaluationMessageFactory.create(create_chat_messages=True)
        msg_unscoped = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_unscoped = msg_unscoped.expected_output_chat_message

        msg_scoped.expected_output_chat_message.tags.add(tag_a, through_defaults={"team": team})
        chat_unscoped.tags.add(tag_a, through_defaults={"team": team})

        dataset = EvaluationDatasetFactory.create(team=team, messages=[msg_scoped, msg_unscoped])
        config = EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team, config=config, type=EvaluationRunType.DELTA)
        run.scoped_messages.add(msg_scoped)

        EvaluationResultFactory.create(team=team, evaluator=evaluator, message=msg_scoped, run=run, output={})

        reverse_stale_tags(run)

        assert not msg_scoped.expected_output_chat_message.tags.filter(pk=tag_a.pk).exists()
        assert chat_unscoped.tags.filter(pk=tag_a.pk).exists()

    def test_preview_run_skips_cleanup(self, team, evaluator, tag_a, tag_rule):
        """PREVIEW runs never trigger cleanup."""
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message

        chat_message.tags.add(tag_a, through_defaults={"team": team})

        run = _build_run(team, evaluator, [message], run_type=EvaluationRunType.PREVIEW)

        reverse_stale_tags(run)

        assert chat_message.tags.filter(pk=tag_a.pk).exists()

    def test_multi_evaluator_stale_tags_from_both_evaluators_removed(self, team, tag_a):
        """possible_tags aggregates rules from all evaluators; stale tags from each are removed."""
        tag_b = EvaluationTagFactory.create(team=team, name="tag-b")
        evaluator_a = EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.MESSAGE)
        evaluator_b = EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.MESSAGE)
        EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=evaluator_a,
            tag=tag_a,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=evaluator_b,
            tag=tag_b,
            field_name="quality",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "low"},
        )
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message

        chat_message.tags.add(tag_a, through_defaults={"team": team})
        chat_message.tags.add(tag_b, through_defaults={"team": team})

        dataset = EvaluationDatasetFactory.create(team=team, messages=[message])
        config = EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[evaluator_a, evaluator_b])
        run = EvaluationRunFactory.create(team=team, config=config)
        EvaluationResultFactory.create(team=team, evaluator=evaluator_a, message=message, run=run, output={})
        EvaluationResultFactory.create(team=team, evaluator=evaluator_b, message=message, run=run, output={})

        reverse_stale_tags(run)

        assert not chat_message.tags.filter(pk=tag_a.pk).exists()
        assert not chat_message.tags.filter(pk=tag_b.pk).exists()

    def test_session_mode_stale_tag_removed_from_chat(self, team, tag_a):
        """Session-mode evaluators target the Chat; stale tags are removed from Chat."""
        session_evaluator = EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.SESSION)
        EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=session_evaluator,
            tag=tag_a,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        session = ExperimentSessionFactory.create(team=team)
        message = EvaluationMessageFactory.create(session=session)
        chat = session.chat

        chat.tags.add(tag_a, through_defaults={"team": team})
        assert chat.tags.filter(pk=tag_a.pk).exists()

        dataset = EvaluationDatasetFactory.create(team=team, messages=[message], evaluation_mode=EvaluationMode.SESSION)
        config = EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[session_evaluator])
        run = EvaluationRunFactory.create(team=team, config=config)
        EvaluationResultFactory.create(team=team, evaluator=session_evaluator, message=message, run=run, output={})

        reverse_stale_tags(run)

        assert not chat.tags.filter(pk=tag_a.pk).exists()
