"""DB integration tests for eval-driven tagging."""

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from apps.annotations.models import CustomTaggedItem, Tag, TagCategories
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

    def test_range_on_choice_field_rejected(self, team, message_evaluator):
        tag = EvaluationTagFactory.create(team=team)
        # 'range' on a choice field is a type mismatch
        rule = EvaluatorTagRule(
            team=team,
            evaluator=message_evaluator,
            tag=tag,
            field_name="sentiment",
            condition_type=ConditionType.RANGE,
            condition_value={"min": 0, "max": 1},
        )
        with pytest.raises(ValidationError):
            rule.clean()

    def test_equals_on_int_field_accepted(self, team, message_evaluator):
        tag = EvaluationTagFactory.create(team=team)
        rule = EvaluatorTagRule(
            team=team,
            evaluator=message_evaluator,
            tag=tag,
            field_name="score",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": 5},
        )
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


# ---- EvaluatorForm schema-drift validation --------------------------------


class TestEvaluatorFormSchemaDrift:
    def test_rule_with_removed_field_blocks_save(self, team, message_evaluator):
        from apps.evaluations.forms import EvaluatorForm  # noqa: PLC0415

        EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=message_evaluator,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        # New output_schema drops "sentiment"
        new_params = {
            "llm_prompt": "prompt",
            "llm_provider_id": 1,
            "llm_provider_model_id": 1,
            "output_schema": {"score": {"type": "int", "description": "d"}},
        }
        form_data = {
            "name": message_evaluator.name,
            "type": "LlmEvaluator",
            "params": new_params,
            "evaluation_mode": message_evaluator.evaluation_mode,
        }
        form = EvaluatorForm(team=team, data=form_data, instance=message_evaluator)
        with patch("apps.evaluations.evaluators.LlmEvaluator.__init__", return_value=None):
            assert not form.is_valid()
        assert any("sentiment" in str(e) for e in form.errors.get("__all__", []))

    def test_rule_with_incompatible_type_blocks_save(self, team, message_evaluator):
        from apps.evaluations.forms import EvaluatorForm  # noqa: PLC0415

        EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=message_evaluator,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        # sentiment is now an int field; equals no longer applies
        new_params = {
            "llm_prompt": "prompt",
            "llm_provider_id": 1,
            "llm_provider_model_id": 1,
            "output_schema": {"sentiment": {"type": "int", "description": "d"}},
        }
        form_data = {
            "name": message_evaluator.name,
            "type": "LlmEvaluator",
            "params": new_params,
            "evaluation_mode": message_evaluator.evaluation_mode,
        }
        form = EvaluatorForm(team=team, data=form_data, instance=message_evaluator)
        with patch("apps.evaluations.evaluators.LlmEvaluator.__init__", return_value=None):
            assert not form.is_valid()


# ---- Tag-rule formset --------------------------------------------------------


class TestEvaluatorTagRuleFormset:
    def test_adds_new_rule_via_formset(self, team, message_evaluator):
        from apps.evaluations.forms import EvaluatorTagRuleFormSet  # noqa: PLC0415

        output_schema = {
            "sentiment": {
                "type": "choice",
                "description": "sent",
                "choices": ["positive", "negative"],
            }
        }
        data = {
            "tag_rules-TOTAL_FORMS": "1",
            "tag_rules-INITIAL_FORMS": "0",
            "tag_rules-MIN_NUM_FORMS": "0",
            "tag_rules-MAX_NUM_FORMS": "1000",
            "tag_rules-0-tag_name": "unacceptable",
            "tag_rules-0-field_name": "sentiment",
            "tag_rules-0-condition_type": ConditionType.EQUALS,
            "tag_rules-0-condition_value_single": "negative",
        }
        formset = EvaluatorTagRuleFormSet(
            data=data,
            instance=message_evaluator,
            team=team,
            output_schema=output_schema,
        )
        assert formset.is_valid(), formset.errors
        rules = formset.save()
        assert len(rules) == 1
        rule = rules[0]
        assert rule.tag.name == "unacceptable"
        assert rule.tag.category == TagCategories.EVALUATIONS.value
        assert rule.tag.is_system_tag is True
        assert rule.condition_value == {"value": "negative"}
        assert rule.evaluator_id == message_evaluator.id

    def test_range_rule_accepts_min_max(self, team, message_evaluator):
        from apps.evaluations.forms import EvaluatorTagRuleFormSet  # noqa: PLC0415

        output_schema = {"score": {"type": "int", "description": "s"}}
        data = {
            "tag_rules-TOTAL_FORMS": "1",
            "tag_rules-INITIAL_FORMS": "0",
            "tag_rules-MIN_NUM_FORMS": "0",
            "tag_rules-MAX_NUM_FORMS": "1000",
            "tag_rules-0-tag_name": "low-score",
            "tag_rules-0-field_name": "score",
            "tag_rules-0-condition_type": ConditionType.RANGE,
            "tag_rules-0-condition_value_min": "0",
            "tag_rules-0-condition_value_max": "3",
        }
        formset = EvaluatorTagRuleFormSet(
            data=data,
            instance=message_evaluator,
            team=team,
            output_schema=output_schema,
        )
        assert formset.is_valid(), formset.errors
        rules = formset.save()
        assert rules[0].condition_value == {"min": 0.0, "max": 3.0}

    def test_empty_rows_are_ignored(self, team, message_evaluator):
        from apps.evaluations.forms import EvaluatorTagRuleFormSet  # noqa: PLC0415

        data = {
            "tag_rules-TOTAL_FORMS": "1",
            "tag_rules-INITIAL_FORMS": "0",
            "tag_rules-MIN_NUM_FORMS": "0",
            "tag_rules-MAX_NUM_FORMS": "1000",
        }
        formset = EvaluatorTagRuleFormSet(
            data=data,
            instance=message_evaluator,
            team=team,
            output_schema={},
        )
        assert formset.is_valid()
        assert formset.save() == []

    def test_missing_value_reports_error(self, team, message_evaluator):
        from apps.evaluations.forms import EvaluatorTagRuleFormSet  # noqa: PLC0415

        data = {
            "tag_rules-TOTAL_FORMS": "1",
            "tag_rules-INITIAL_FORMS": "0",
            "tag_rules-MIN_NUM_FORMS": "0",
            "tag_rules-MAX_NUM_FORMS": "1000",
            "tag_rules-0-tag_name": "needs-value",
            "tag_rules-0-field_name": "sentiment",
            "tag_rules-0-condition_type": ConditionType.EQUALS,
        }
        formset = EvaluatorTagRuleFormSet(
            data=data,
            instance=message_evaluator,
            team=team,
            output_schema={
                "sentiment": {
                    "type": "choice",
                    "description": "s",
                    "choices": ["positive"],
                }
            },
        )
        assert not formset.is_valid()
