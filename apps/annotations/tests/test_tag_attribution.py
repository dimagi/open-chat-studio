"""Tests for eval-driven tag tooltip attribution (who/what applied a tag)."""

import pytest
from django.utils import timezone

from apps.annotations.tag_attribution import attach_tag_attributions
from apps.evaluations.models import (
    ConditionType,
    EvaluationMode,
    EvaluationRunStatus,
    EvaluationRunType,
)
from apps.evaluations.tagging import apply_rules_to_result, archive_superseded_runs, undo_run_tags
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


def _setup(team, mode=EvaluationMode.MESSAGE):
    evaluator = EvaluatorFactory.create(team=team, evaluation_mode=mode, name="Sentiment")
    rule = EvaluatorTagRuleFactory.create(
        team=team,
        evaluator=evaluator,
        field_name="sentiment",
        condition_type=ConditionType.EQUALS,
        condition_value={"value": "negative"},
        tag__name="bad",
    )
    if mode == EvaluationMode.SESSION:
        session = ExperimentSessionFactory.create(team=team)
        message = EvaluationMessageFactory.create(session=session)
    else:
        message = EvaluationMessageFactory.create(create_chat_messages=True)
    dataset = EvaluationDatasetFactory.create(team=team, messages=[message])
    config = EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[evaluator])
    return config, evaluator, rule, message


def _run(config, evaluator, message):
    run = EvaluationRunFactory.create(
        team=config.team,
        config=config,
        status=EvaluationRunStatus.COMPLETED,
        type=EvaluationRunType.FULL,
        finished_at=timezone.now(),
    )
    result = EvaluationResultFactory.create(
        team=config.team, evaluator=evaluator, message=message, run=run, output={"result": {"sentiment": "negative"}}
    )
    apply_rules_to_result(result, evaluator, message)
    return run


class TestAttachTagAttributions:
    def test_message_mode_attribution(self, team):
        config, evaluator, rule, message = _setup(team, mode=EvaluationMode.MESSAGE)
        run = _run(config, evaluator, message)
        chat_message = message.expected_output_chat_message

        attach_tag_attributions([chat_message])

        assert chat_message.prefetched_tag_attributions == {rule.tag_id: f"evaluator 'Sentiment' (run #{run.id})"}

    def test_session_mode_attribution(self, team):
        config, evaluator, rule, message = _setup(team, mode=EvaluationMode.SESSION)
        run = _run(config, evaluator, message)
        chat = message.session.chat

        attach_tag_attributions([chat])

        assert chat.prefetched_tag_attributions == {rule.tag_id: f"evaluator 'Sentiment' (run #{run.id})"}

    def test_archived_tags_excluded(self, team):
        """A tag undone (archived) must not show eval attribution any longer."""
        config, evaluator, rule, message = _setup(team, mode=EvaluationMode.MESSAGE)
        run = _run(config, evaluator, message)
        chat_message = message.expected_output_chat_message

        undo_run_tags(run)  # archives run's AppliedTags

        attach_tag_attributions([chat_message])
        assert chat_message.prefetched_tag_attributions == {}

    def test_latest_run_wins(self, team):
        config, evaluator, rule, message = _setup(team, mode=EvaluationMode.MESSAGE)
        _run(config, evaluator, message)  # run1 -> will be superseded
        run2 = _run(config, evaluator, message)
        archive_superseded_runs(run2)
        chat_message = message.expected_output_chat_message

        attach_tag_attributions([chat_message])
        assert chat_message.prefetched_tag_attributions == {rule.tag_id: f"evaluator 'Sentiment' (run #{run2.id})"}

    def test_no_attribution_for_untagged_object(self, team):
        config, evaluator, rule, message = _setup(team, mode=EvaluationMode.MESSAGE)
        _run(config, evaluator, message)
        # A different, untagged message
        other = EvaluationMessageFactory.create(create_chat_messages=True)

        attach_tag_attributions([other.expected_output_chat_message])
        assert other.expected_output_chat_message.prefetched_tag_attributions == {}


class TestPrefetchedTagsJsonAttribution:
    def test_attribution_used_for_added_by(self, team):
        config, evaluator, rule, message = _setup(team, mode=EvaluationMode.MESSAGE)
        run = _run(config, evaluator, message)
        chat_message = message.expected_output_chat_message
        # Build the prefetch shape prefetched_tags_json expects
        chat_message.prefetched_tagged_items = list(chat_message.tagged_items.select_related("tag", "user").all())
        attach_tag_attributions([chat_message])

        tags = chat_message.prefetched_tags_json
        bad = next(t for t in tags if t["id"] == rule.tag_id)
        assert bad["added_by"] == f"evaluator 'Sentiment' (run #{run.id})"
