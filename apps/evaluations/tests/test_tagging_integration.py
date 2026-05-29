"""DB integration tests for eval-driven tagging."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.annotations.models import CustomTaggedItem, Tag
from apps.evaluations.forms import EvaluatorForm, EvaluatorTagRuleFormSet
from apps.evaluations.models import (
    AppliedTag,
    ConditionType,
    EvaluationMode,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationRunType,
    EvaluatorTagRule,
)
from apps.evaluations.tagging import _message_ids_by_latest_prior_run, apply_rules_to_result, undo_run_tags
from apps.evaluations.tasks import _maybe_apply_tag_rules
from apps.utils.factories.evaluations import (
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
def message_evaluator(team):
    return EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.MESSAGE)


@pytest.fixture()
def session_evaluator(team):
    return EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.SESSION)


# ---- EvaluatorTagRule.clean() ---------------------------------------------


class TestEvaluatorTagRuleClean:
    def test_system_tag_rejected(self, team, message_evaluator):
        system_tag = Tag.objects.create(team=team, name="experiment_version_x", is_system_tag=True, category="")
        rule = EvaluatorTagRule(
            team=team,
            evaluator=message_evaluator,
            tag=system_tag,
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


# ---- Transaction rollback -------------------------------------------------


class TestTransactionRollback:
    def test_bulk_create_failure_rolls_back(self, team, message_evaluator):
        """If AppliedTag.bulk_create raises, CustomTaggedItem writes roll back too."""
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
        assert rule.tag.category == ""
        assert rule.tag.is_system_tag is False
        assert rule.condition_value == {"value": "negative"}
        assert rule.evaluator_id == message_evaluator.id

    def test_range_rule_accepts_min_max(self, team, message_evaluator):
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


# ---- undo_run_tags ---------------------------------------------------------


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

    def _make_run(self, team, config, output, rule_that_fires, message):
        """Create a COMPLETED FULL run, apply the tag rule, return the run."""
        run = EvaluationRunFactory.create(
            team=team,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.FULL,
            finished_at=timezone.now(),
        )
        result = EvaluationResultFactory.create(
            team=team,
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
        run = self._make_run(team, config, {"result": {"sentiment": "negative"}}, rule_neg, message)

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
        prev_run = self._make_run(team, config, {"result": {"sentiment": "negative"}}, rule_neg, message)
        _stamp(prev_run, timedelta(hours=-1))

        # Build current run (applied "good")
        # No _stamp needed: prev_run is at now()-1h, so curr_run's natural created_at is later.
        curr_run = self._make_run(team, config, {"result": {"sentiment": "positive"}}, rule_pos, message)

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
        run = self._make_run(team, config, {"result": {"sentiment": "negative"}}, rule_neg, message)

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
        run_a = self._make_run(team, config, {"result": {"sentiment": "negative"}}, rule_neg, message)
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
        run_c = self._make_run(team, config, {"result": {"sentiment": "positive"}}, rule_pos, message)
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

    def _make_run_with_results(self, team, config, evaluator, messages, run_type=EvaluationRunType.FULL):
        run = EvaluationRunFactory.create(
            team=team,
            config=config,
            status=EvaluationRunStatus.COMPLETED,
            type=run_type,
            finished_at=timezone.now(),
        )
        for message in messages:
            EvaluationResultFactory.create(
                team=team, evaluator=evaluator, message=message, run=run, output={"result": {}}
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

        old_run = self._make_run_with_results(team, config, evaluator, [message])
        _stamp(old_run, timedelta(hours=-3))

        recent_run = self._make_run_with_results(team, config, evaluator, [message])
        _stamp(recent_run, timedelta(hours=-1))

        # The current run has to be later than both
        curr_run = self._make_run_with_results(team, config, evaluator, [message])

        result = _message_ids_by_latest_prior_run(curr_run, [message.pk])
        assert dict(result) == {recent_run.pk: {message.pk}}

    def test_groups_messages_sharing_a_previous_run(self, team):
        """Two messages whose latest prior run is the same should be grouped under that run_id."""
        config, evaluator = self._make_config(team)
        m1 = EvaluationMessageFactory.create(create_chat_messages=True)
        m2 = EvaluationMessageFactory.create(create_chat_messages=True)

        shared_prior = self._make_run_with_results(team, config, evaluator, [m1, m2])
        _stamp(shared_prior, timedelta(hours=-1))

        curr_run = self._make_run_with_results(team, config, evaluator, [m1, m2])

        result = _message_ids_by_latest_prior_run(curr_run, [m1.pk, m2.pk])
        assert dict(result) == {shared_prior.pk: {m1.pk, m2.pk}}

    def test_walks_past_delta_that_skipped_message(self, team):
        """A DELTA predecessor that did not touch a message must be skipped per-message."""
        config, evaluator = self._make_config(team)
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        other_message = EvaluationMessageFactory.create(create_chat_messages=True)

        # Run A (FULL): evaluated `message`
        run_a = self._make_run_with_results(team, config, evaluator, [message])
        _stamp(run_a, timedelta(hours=-3))

        # Run B (DELTA): evaluated only `other_message`
        self._make_run_with_results(team, config, evaluator, [other_message], run_type=EvaluationRunType.DELTA)

        # Current run
        curr_run = self._make_run_with_results(team, config, evaluator, [message])

        result = _message_ids_by_latest_prior_run(curr_run, [message.pk])
        # For `message`, the latest prior run is Run A — Run B skipped it.
        assert dict(result) == {run_a.pk: {message.pk}}

    def test_excludes_preview_and_non_completed_runs(self, team):
        """PREVIEW runs and non-COMPLETED runs must never appear as previous-state."""
        config, evaluator = self._make_config(team)
        message = EvaluationMessageFactory.create(create_chat_messages=True)

        preview_run = self._make_run_with_results(
            team, config, evaluator, [message], run_type=EvaluationRunType.PREVIEW
        )
        _stamp(preview_run, timedelta(hours=-2))

        # A pending run evaluated the message but is not COMPLETED
        pending_run = EvaluationRunFactory.create(
            team=team, config=config, status=EvaluationRunStatus.PENDING, type=EvaluationRunType.FULL
        )
        EvaluationResultFactory.create(
            team=team, evaluator=evaluator, message=message, run=pending_run, output={"result": {}}
        )
        _stamp(pending_run, timedelta(hours=-2))

        curr_run = self._make_run_with_results(team, config, evaluator, [message])

        assert _message_ids_by_latest_prior_run(curr_run, [message.pk]) == {}

    def test_excludes_runs_from_other_configs(self, team):
        """Only prior runs of the *same* config count as previous state."""
        config_a, evaluator_a = self._make_config(team)
        config_b, evaluator_b = self._make_config(team)
        message = EvaluationMessageFactory.create(create_chat_messages=True)

        # Prior run on config B that happens to have evaluated the same message
        other_config_run = self._make_run_with_results(team, config_b, evaluator_b, [message])
        _stamp(other_config_run, timedelta(hours=-2))

        # Current run on config A
        curr_run = self._make_run_with_results(team, config_a, evaluator_a, [message])

        assert _message_ids_by_latest_prior_run(curr_run, [message.pk]) == {}

    def test_excludes_runs_at_or_after_current(self, team):
        """Only runs strictly older than the current run are candidates."""
        config, evaluator = self._make_config(team)
        message = EvaluationMessageFactory.create(create_chat_messages=True)

        curr_run = self._make_run_with_results(team, config, evaluator, [message])

        # Run created after curr_run is not a candidate
        later_run = self._make_run_with_results(team, config, evaluator, [message])
        _stamp(later_run, timedelta(hours=1))

        assert _message_ids_by_latest_prior_run(curr_run, [message.pk]) == {}

    def test_only_returns_rows_for_requested_messages(self, team):
        """Messages outside `message_ids` must not appear, even if they have prior runs."""
        config, evaluator = self._make_config(team)
        m1 = EvaluationMessageFactory.create(create_chat_messages=True)
        m2 = EvaluationMessageFactory.create(create_chat_messages=True)

        prior_run = self._make_run_with_results(team, config, evaluator, [m1, m2])
        _stamp(prior_run, timedelta(hours=-1))

        curr_run = self._make_run_with_results(team, config, evaluator, [m1, m2])

        result = _message_ids_by_latest_prior_run(curr_run, [m1.pk])
        assert dict(result) == {prior_run.pk: {m1.pk}}

    def test_orders_by_finished_at_not_created_at(self, team):
        """When runs overlap, the run that *finished* most recently wins, even if it was
        created earlier. Runs can overlap (no concurrency guard), so completion time — not
        creation time — reflects which prior tag state is the most recent."""
        config, evaluator = self._make_config(team)
        message = EvaluationMessageFactory.create(create_chat_messages=True)

        # long_run: created earliest (-3h) but finished latest (-1h) — it overlapped.
        long_run = self._make_run_with_results(team, config, evaluator, [message])
        _stamp(long_run, timedelta(hours=-3), finished_delta=timedelta(hours=-1))

        # short_run: created later (-2h) but finished earlier (-90min).
        short_run = self._make_run_with_results(team, config, evaluator, [message])
        _stamp(short_run, timedelta(hours=-2), finished_delta=timedelta(minutes=-90))

        curr_run = self._make_run_with_results(team, config, evaluator, [message])

        # By finished_at, long_run (-1h) is the most recent prior state, not short_run.
        result = _message_ids_by_latest_prior_run(curr_run, [message.pk])
        assert dict(result) == {long_run.pk: {message.pk}}

    def test_tie_broken_deterministically_by_run_id(self, team):
        """Two prior runs finishing at the exact same instant must resolve deterministically:
        the higher run_id wins (stable tie-breaker for DISTINCT ON)."""
        config, evaluator = self._make_config(team)
        message = EvaluationMessageFactory.create(create_chat_messages=True)

        first = self._make_run_with_results(team, config, evaluator, [message])
        second = self._make_run_with_results(team, config, evaluator, [message])
        # Force identical created_at AND finished_at on both, so only the run_id tie-breaker
        # can separate them — otherwise DISTINCT ON picks an arbitrary row.
        tied = timezone.now() - timedelta(hours=1)
        EvaluationRun.objects.filter(pk__in=[first.pk, second.pk]).update(created_at=tied, finished_at=tied)
        first.refresh_from_db()
        second.refresh_from_db()

        curr_run = self._make_run_with_results(team, config, evaluator, [message])

        winner = max(first.pk, second.pk)
        result = _message_ids_by_latest_prior_run(curr_run, [message.pk])
        assert dict(result) == {winner: {message.pk}}
