"""End-to-end tests for the evaluator create/edit views and the tag-rule formset wiring."""

import json
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.evaluations.models import ConditionType
from apps.utils.factories.evaluations import EvaluatorFactory, EvaluatorTagRuleFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def client_with_user(team):
    client = Client()
    client.force_login(team.members.first())
    return client


@pytest.fixture()
def evaluator(team):
    return EvaluatorFactory.create(team=team)


def _edit_url(team, evaluator):
    return reverse("evaluations:evaluator_edit", args=[team.slug, evaluator.pk])


def _params(output_schema):
    return {
        "llm_prompt": "prompt",
        "llm_provider_id": 1,
        "llm_provider_model_id": 1,
        "output_schema": output_schema,
    }


def _post_data(evaluator, output_schema, rule_rows):
    data = {
        "name": evaluator.name,
        "type": "LlmEvaluator",
        "params": json.dumps(_params(output_schema)),
        "evaluation_mode": evaluator.evaluation_mode,
        "tag_rules-TOTAL_FORMS": str(len(rule_rows)),
        "tag_rules-INITIAL_FORMS": str(len([r for r in rule_rows if r.get("id")])),
        "tag_rules-MIN_NUM_FORMS": "0",
        "tag_rules-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rule_rows):
        for key, value in row.items():
            data[f"tag_rules-{i}-{key}"] = value
    return data


@pytest.mark.django_db()
class TestEditEvaluatorSchemaDrift:
    def test_renaming_field_and_updating_rule_in_same_submit_saves(self, client_with_user, team, evaluator):
        """Renaming an output field and updating the tag rule to match, in one POST, succeeds."""
        rule = EvaluatorTagRuleFactory.create(team=team, evaluator=evaluator, field_name="old_field")
        new_schema = {"new_field": {"type": "choice", "description": "d", "choices": ["negative", "positive"]}}
        data = _post_data(
            evaluator,
            new_schema,
            [
                {
                    "id": str(rule.pk),
                    "tag_name": rule.tag.name,
                    "field_name": "new_field",
                    "condition_type": ConditionType.EQUALS,
                    "condition_value_single": "negative",
                }
            ],
        )

        with patch("apps.evaluations.evaluators.LlmEvaluator.__init__", return_value=None):
            response = client_with_user.post(_edit_url(team, evaluator), data)

        assert response.status_code == 302, getattr(response, "context_data", None)
        rule.refresh_from_db()
        assert rule.field_name == "new_field"
        evaluator.refresh_from_db()
        assert evaluator.params["output_schema"] == new_schema

    def test_schema_change_breaking_a_rule_is_blocked(self, client_with_user, team, evaluator):
        """Removing a field that an existing rule references re-renders with errors and saves nothing."""
        rule = EvaluatorTagRuleFactory.create(team=team, evaluator=evaluator, field_name="sentiment")
        old_schema = evaluator.params["output_schema"]
        data = _post_data(
            evaluator,
            {"score": {"type": "int", "description": "d"}},  # drops "sentiment"
            [
                {
                    "id": str(rule.pk),
                    "tag_name": rule.tag.name,
                    "field_name": "sentiment",
                    "condition_type": ConditionType.EQUALS,
                    "condition_value_single": "negative",
                }
            ],
        )

        with patch("apps.evaluations.evaluators.LlmEvaluator.__init__", return_value=None):
            response = client_with_user.post(_edit_url(team, evaluator), data)

        assert response.status_code == 200
        evaluator.refresh_from_db()
        assert evaluator.params["output_schema"] == old_schema
        rule.refresh_from_db()
        assert rule.field_name == "sentiment"

    def test_deleting_stale_rule_with_schema_change_in_same_submit_saves(self, client_with_user, team, evaluator):
        """Deleting the incompatible rule in the same POST as the schema change succeeds in one trip."""
        rule = EvaluatorTagRuleFactory.create(team=team, evaluator=evaluator, field_name="sentiment")
        new_schema = {"score": {"type": "int", "description": "d"}}
        data = _post_data(
            evaluator,
            new_schema,
            [
                {
                    "id": str(rule.pk),
                    "tag_name": rule.tag.name,
                    "field_name": "sentiment",
                    "condition_type": ConditionType.EQUALS,
                    "condition_value_single": "negative",
                    "DELETE": "on",
                }
            ],
        )

        with patch("apps.evaluations.evaluators.LlmEvaluator.__init__", return_value=None):
            response = client_with_user.post(_edit_url(team, evaluator), data)

        assert response.status_code == 302, getattr(response, "context_data", None)
        evaluator.refresh_from_db()
        assert evaluator.params["output_schema"] == new_schema
        assert not evaluator.tag_rules.filter(pk=rule.pk).exists()
