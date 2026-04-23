"""DB integration tests for eval-driven tagging."""

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from apps.annotations.models import CustomTaggedItem, Tag
from apps.evaluations.models import (
    AppliedTag,
    ConditionType,
    EvaluationMode,
    EvaluationRunType,
    EvaluatorTagRule,
)
from apps.evaluations.tagging import apply_rules_to_result
from apps.utils.factories.evaluations import (
    AppliedTagFactory,
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
def message_evaluator(team):
    return EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.MESSAGE)


@pytest.fixture()
def session_evaluator(team):
    return EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.SESSION)


# ---- EvaluatorTagRule.clean() ---------------------------------------------


class TestEvaluatorTagRuleClean:
    def test_wrong_category_rejected(self, team, message_evaluator):
        user_tag = Tag.objects.create(team=team, name="foo", is_system_tag=False, category="")
        rule = EvaluatorTagRule(
            team=team,
            evaluator=message_evaluator,
            tag=user_tag,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        with pytest.raises(ValidationError):
            rule.clean()

    def test_cross_team_tag_rejected(self, team, message_evaluator):
        other_team = TeamFactory.create()
        tag = EvaluationTagFactory.create(team=other_team)
        rule = EvaluatorTagRule(
            team=team,
            evaluator=message_evaluator,
            tag=tag,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        with pytest.raises(ValidationError):
            rule.clean()

    def test_wrong_field_type_for_condition_rejected(self, team, message_evaluator):
        tag = EvaluationTagFactory.create(team=team)
        # 'equals' on an int field is a type mismatch
        rule = EvaluatorTagRule(
            team=team,
            evaluator=message_evaluator,
            tag=tag,
            field_name="score",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": 5},
        )
        with pytest.raises(ValidationError):
            rule.clean()

    def test_unknown_field_rejected(self, team, message_evaluator):
        tag = EvaluationTagFactory.create(team=team)
        rule = EvaluatorTagRule(
            team=team,
            evaluator=message_evaluator,
            tag=tag,
            field_name="not_in_schema",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "x"},
        )
        with pytest.raises(ValidationError):
            rule.clean()

    def test_valid_rule_clean_ok(self, team, message_evaluator):
        tag = EvaluationTagFactory.create(team=team)
        rule = EvaluatorTagRule(
            team=team,
            evaluator=message_evaluator,
            tag=tag,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        rule.clean()


# ---- apply_rules_to_result -------------------------------------------------


class TestApplyRulesToResult:
    def _build_result(self, team, evaluator, message, output):
        run = EvaluationRunFactory.create(team=team)
        return EvaluationResultFactory.create(
            team=team,
            evaluator=evaluator,
            message=message,
            run=run,
            output=output,
        )

    def test_happy_path_creates_custom_tagged_item_and_applied_tag(self, team, message_evaluator):
        rule = EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=message_evaluator,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        result = self._build_result(
            team,
            message_evaluator,
            message,
            {"result": {"sentiment": "negative"}},
        )

        apply_rules_to_result(result, message_evaluator, message)

        chat_message = message.expected_output_chat_message
        assert chat_message.tags.filter(pk=rule.tag_id).exists()
        assert AppliedTag.objects.filter(rule=rule, evaluation_result=result).count() == 1

    def test_idempotency(self, team, message_evaluator):
        rule = EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=message_evaluator,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message
        output = {"result": {"sentiment": "negative"}}

        first = self._build_result(team, message_evaluator, message, output)
        apply_rules_to_result(first, message_evaluator, message)

        second = self._build_result(team, message_evaluator, message, output)
        apply_rules_to_result(second, message_evaluator, message)

        # CustomTaggedItem is idempotent via unique constraint
        assert (
            CustomTaggedItem.objects.filter(
                object_id=chat_message.pk,
                tag=rule.tag,
            ).count()
            == 1
        )
        # AppliedTag records each application
        assert AppliedTag.objects.filter(rule=rule).count() == 2

    def test_removal_semantics_between_runs(self, team, message_evaluator):
        rule = EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=message_evaluator,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message

        r1 = self._build_result(team, message_evaluator, message, {"result": {"sentiment": "negative"}})
        apply_rules_to_result(r1, message_evaluator, message)
        assert chat_message.tags.filter(pk=rule.tag_id).exists()

        r2 = self._build_result(team, message_evaluator, message, {"result": {"sentiment": "positive"}})
        apply_rules_to_result(r2, message_evaluator, message)
        assert not chat_message.tags.filter(pk=rule.tag_id).exists()

    def test_removal_only_touches_evaluations_category(self, team, message_evaluator):
        rule = EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=message_evaluator,
            tag__name="foo",
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        # user tag with the same name — different Tag row due to unique_together
        user_tag = Tag.objects.create(team=team, name="foo", is_system_tag=False, category="")
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message
        chat_message.tags.add(user_tag, through_defaults={"team": team})

        r1 = self._build_result(team, message_evaluator, message, {"result": {"sentiment": "negative"}})
        apply_rules_to_result(r1, message_evaluator, message)
        assert chat_message.tags.filter(pk=rule.tag_id).exists()
        assert chat_message.tags.filter(pk=user_tag.pk).exists()

        # Cleanup pass: result now says positive — eval tag removed, user tag preserved.
        r2 = self._build_result(team, message_evaluator, message, {"result": {"sentiment": "positive"}})
        apply_rules_to_result(r2, message_evaluator, message)
        assert not chat_message.tags.filter(pk=rule.tag_id).exists()
        assert chat_message.tags.filter(pk=user_tag.pk).exists()

    def test_null_target_no_op(self, team, message_evaluator):
        EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=message_evaluator,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        # CSV-imported messages have no expected_output_chat_message
        message = EvaluationMessageFactory.create()
        result = self._build_result(team, message_evaluator, message, {"result": {"sentiment": "negative"}})
        apply_rules_to_result(result, message_evaluator, message)

        assert AppliedTag.objects.count() == 0

    def test_session_mode_tags_chat(self, team, session_evaluator):
        rule = EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=session_evaluator,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        session = ExperimentSessionFactory.create(team=team)
        message = EvaluationMessageFactory.create(session=session)
        result = self._build_result(team, session_evaluator, message, {"result": {"sentiment": "negative"}})

        apply_rules_to_result(result, session_evaluator, message)

        assert session.chat.tags.filter(pk=rule.tag_id).exists()
        assert AppliedTag.objects.filter(rule=rule, evaluation_result=result).count() == 1

    def test_concurrent_evaluators_both_land(self, team):
        evaluator_a = EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.MESSAGE)
        evaluator_b = EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.MESSAGE)
        rule_a = EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=evaluator_a,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
            tag__name="eval-a",
        )
        rule_b = EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=evaluator_b,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
            tag__name="eval-b",
        )
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message

        result_a = self._build_result(team, evaluator_a, message, {"result": {"sentiment": "negative"}})
        apply_rules_to_result(result_a, evaluator_a, message)
        result_b = self._build_result(team, evaluator_b, message, {"result": {"sentiment": "negative"}})
        apply_rules_to_result(result_b, evaluator_b, message)

        tag_ids = set(chat_message.tags.values_list("id", flat=True))
        assert rule_a.tag_id in tag_ids
        assert rule_b.tag_id in tag_ids

    def test_missing_field_does_not_raise(self, team, message_evaluator):
        EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=message_evaluator,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        result = self._build_result(team, message_evaluator, message, {"result": {}})
        apply_rules_to_result(result, message_evaluator, message)
        assert AppliedTag.objects.count() == 0


# ---- Task-level skips ------------------------------------------------------


class TestTaskLevelSkips:
    """The task-level layer skips preview runs and error outputs."""

    def test_preview_run_skips_tagging(self, team, message_evaluator):
        from apps.evaluations.tasks import _maybe_apply_tag_rules  # noqa: PLC0415

        EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=message_evaluator,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        run = EvaluationRunFactory.create(team=team, type=EvaluationRunType.PREVIEW)
        result = EvaluationResultFactory.create(
            team=team,
            evaluator=message_evaluator,
            message=message,
            run=run,
            output={"result": {"sentiment": "negative"}},
        )

        _maybe_apply_tag_rules(run, message_evaluator, result, message)
        assert AppliedTag.objects.count() == 0

    def test_error_output_skips_tagging(self, team, message_evaluator):
        from apps.evaluations.tasks import _maybe_apply_tag_rules  # noqa: PLC0415

        EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=message_evaluator,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        run = EvaluationRunFactory.create(team=team, type=EvaluationRunType.FULL)
        result = EvaluationResultFactory.create(
            team=team,
            evaluator=message_evaluator,
            message=message,
            run=run,
            output={"error": "boom"},
        )

        _maybe_apply_tag_rules(run, message_evaluator, result, message)
        assert AppliedTag.objects.count() == 0


# ---- Rule-delete / rule-update cleanup history ----------------------------


class TestAppliedTagHistory:
    def test_rule_delete_cascades_applied_tags(self, team, message_evaluator):
        rule = EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=message_evaluator,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        AppliedTagFactory.create(team=team, rule=rule)
        assert AppliedTag.objects.count() == 1
        rule.delete()
        assert AppliedTag.objects.count() == 0

    def test_evaluator_delete_cascades_rules_and_applied_tags(self, team, message_evaluator):
        rule = EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=message_evaluator,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        AppliedTagFactory.create(team=team, rule=rule)
        message_evaluator.delete()
        assert EvaluatorTagRule.objects.count() == 0
        assert AppliedTag.objects.count() == 0


# ---- Transaction rollback -------------------------------------------------


class TestTransactionRollback:
    def test_bulk_create_failure_rolls_back(self, team, message_evaluator):
        """If AppliedTag.bulk_create raises, CustomTaggedItem writes roll back too."""
        from django.db import transaction  # noqa: PLC0415

        from apps.evaluations.tasks import _maybe_apply_tag_rules  # noqa: PLC0415

        rule = EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=message_evaluator,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message
        run = EvaluationRunFactory.create(team=team, type=EvaluationRunType.FULL)

        def _create_and_tag():
            with transaction.atomic():
                result = EvaluationResultFactory.create(
                    team=team,
                    evaluator=message_evaluator,
                    message=message,
                    run=run,
                    output={"result": {"sentiment": "negative"}},
                )
                _maybe_apply_tag_rules(run, message_evaluator, result, message)

        with patch(
            "apps.evaluations.models.AppliedTag.objects.bulk_create",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(RuntimeError):
                _create_and_tag()

        assert not chat_message.tags.filter(pk=rule.tag_id).exists()
        assert AppliedTag.objects.count() == 0
