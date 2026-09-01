"""End-to-end tests for the evaluator create/edit views and the tag-rule formset wiring."""

import json
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.evaluations import evaluators
from apps.evaluations.models import ConditionType
from apps.evaluations.views.evaluator_views import _get_evaluator_schema
from apps.service_providers.llm_service.default_models import get_default_model
from apps.service_providers.models import LlmProviderModel, LlmProviderTypes
from apps.utils.factories.evaluations import (
    EvaluationMessageFactory,
    EvaluatorFactory,
    EvaluatorTagRuleFactory,
)
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
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
    """An LLM evaluator pointing at providers this team is actually allowed to use."""
    return EvaluatorFactory.create(team=team)


def _edit_url(team, evaluator):
    return reverse("evaluations:evaluator_edit", args=[team.slug, evaluator.pk])


def _params(output_schema):
    return {"llm_prompt": "prompt", "output_schema": output_schema}


def _post_data(evaluator, output_schema, rule_rows, params=None, **overrides):
    data = {
        "name": evaluator.name,
        "type": "LlmEvaluator",
        "params": json.dumps(params if params is not None else _params(output_schema)),
        "evaluation_mode": evaluator.evaluation_mode,
        "llm_provider": evaluator.llm_provider_id,
        "llm_provider_model": evaluator.llm_provider_model_id,
        "tag_rules-TOTAL_FORMS": str(len(rule_rows)),
        "tag_rules-INITIAL_FORMS": str(len([r for r in rule_rows if r.get("id")])),
        "tag_rules-MIN_NUM_FORMS": "0",
        "tag_rules-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rule_rows):
        for key, value in row.items():
            data[f"tag_rules-{i}-{key}"] = value
    return data | overrides


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


@pytest.mark.parametrize(
    ("evaluator_class", "requires_provider"),
    [
        pytest.param(evaluators.LlmEvaluator, True, id="llm-evaluator"),
        pytest.param(evaluators.PythonEvaluator, False, id="python-evaluator"),
    ],
)
def test_the_rendered_schema_flags_the_provider_instead_of_listing_its_ids(evaluator_class, requires_provider):
    """The picker is driven by the flag; the ids are form fields, so they are not parameters."""
    schema = _get_evaluator_schema(evaluator_class)

    assert schema["requires_llm_provider"] is requires_provider
    assert not set(evaluators.LLM_PROVIDER_FIELDS) & set(schema["properties"])
    assert not set(evaluators.LLM_PROVIDER_FIELDS) & set(schema["required"])


@pytest.mark.parametrize(
    "evaluator_class",
    [
        pytest.param(evaluators.LlmEvaluator, id="llm-evaluator"),
        pytest.param(evaluators.PythonEvaluator, id="python-evaluator"),
    ],
)
def test_the_rendered_schema_keeps_no_reference_to_the_definitions_it_drops(evaluator_class):
    """`_get_evaluator_schema` inlines the `$ref`s and then drops `$defs`, and substitution goes only
    one level deep, so a param whose model nests another model two deep leaves an inner `$ref` the
    form builder cannot follow. Mirrors the node-schema guard in `apps/pipelines/tests/test_schemas.py`."""
    schema = _get_evaluator_schema(evaluator_class)

    assert "$ref" not in json.dumps(schema)


@pytest.mark.django_db()
class TestEvaluatorPickerInitialState:
    """What the provider picker starts on — it is seeded from the FKs, not from params."""

    def test_editing_starts_on_the_saved_selection(self, client_with_user, team, evaluator):
        response = client_with_user.get(_edit_url(team, evaluator))

        assert response.context_data["llm_provider_selection"] == {
            "llm_provider_id": evaluator.llm_provider_id,
            "llm_provider_model_id": evaluator.llm_provider_model_id,
        }

    def test_creating_starts_on_the_teams_first_provider(self, client_with_user, team):
        """The pair has to be one the form accepts, so the model must be one this provider can serve."""
        provider = LlmProviderFactory.create(team=team)
        # Don't lean on the seeded global models: they are absent under --no-migrations.
        LlmProviderModelFactory.create(team=team, type=provider.type, name=get_default_model(provider.type).name)

        response = client_with_user.get(reverse("evaluations:evaluator_new", args=[team.slug]))

        selection = response.context_data["llm_provider_selection"]
        assert selection["llm_provider_id"] == provider.id
        model = LlmProviderModel.objects.get(id=selection["llm_provider_model_id"])
        assert (model.type, model.name) == (provider.type, get_default_model(provider.type).name)

    def test_creating_with_an_embedding_only_provider_starts_on_no_model(self, client_with_user, team):
        """Voyage has no chat models to default to, and that must leave the page renderable."""
        provider = LlmProviderFactory.create(team=team, type=str(LlmProviderTypes.voyage))

        response = client_with_user.get(reverse("evaluations:evaluator_new", args=[team.slug]))

        assert response.context_data["llm_provider_selection"] == {
            "llm_provider_id": provider.id,
            "llm_provider_model_id": None,
        }

    def test_editing_an_evaluator_whose_provider_was_deleted_starts_on_nothing(self, client_with_user, team, evaluator):
        """Defaulting here would repoint the evaluator at an unchosen provider on the next Update."""
        evaluator.llm_provider.delete()
        LlmProviderFactory.create(team=team)  # a default is available, and must not be used

        response = client_with_user.get(_edit_url(team, evaluator))

        assert response.context_data["llm_provider_selection"]["llm_provider_id"] is None

    def test_a_cleared_selection_stays_cleared_on_re_render(self, client_with_user, team, evaluator):
        """Falling back to the default here would show a provider next to "select a provider"."""
        response = client_with_user.post(
            _edit_url(team, evaluator), _post_data(evaluator, {}, [], llm_provider="", llm_provider_model="")
        )

        assert response.context_data["llm_provider_selection"] == {
            "llm_provider_id": "",
            "llm_provider_model_id": "",
        }


@pytest.mark.django_db()
class TestEvaluatorLlmProviderSelection:
    def test_a_valid_selection_repoints_the_fks(self, client_with_user, team, evaluator):
        new_provider = LlmProviderFactory.create(team=team)
        new_model = LlmProviderModelFactory.create(team=team, type=new_provider.type)

        with patch("apps.evaluations.evaluators.LlmEvaluator.__init__", return_value=None):
            response = client_with_user.post(
                _edit_url(team, evaluator),
                _post_data(evaluator, {}, [], llm_provider=new_provider.id, llm_provider_model=new_model.id),
            )

        assert response.status_code == 302, getattr(response, "context_data", None)
        evaluator.refresh_from_db()
        assert evaluator.llm_provider_id == new_provider.id
        assert evaluator.llm_provider_model_id == new_model.id

    def test_ids_posted_inside_params_are_discarded(self, client_with_user, team, evaluator):
        """A stale tab may still submit them; the FK fields are the only record."""
        other_provider = LlmProviderFactory.create(team=team)
        params = _params({}) | {"llm_provider_id": other_provider.id, "llm_provider_model_id": 999}

        with patch("apps.evaluations.evaluators.LlmEvaluator.__init__", return_value=None):
            response = client_with_user.post(_edit_url(team, evaluator), _post_data(evaluator, {}, [], params=params))

        assert response.status_code == 302, getattr(response, "context_data", None)
        evaluator.refresh_from_db()
        assert "llm_provider_id" not in evaluator.params
        assert "llm_provider_model_id" not in evaluator.params
        assert evaluator.llm_provider_id != other_provider.id

    def test_a_model_from_another_provider_type_is_rejected(self, client_with_user, team, evaluator):
        """The picker only offers matching types, so a mismatch means a hand-crafted post."""
        mismatched_type = next(t for t in LlmProviderTypes if str(t) != evaluator.llm_provider.type)
        mismatched_model = LlmProviderModelFactory.create(team=team, type=str(mismatched_type))
        original_model_id = evaluator.llm_provider_model_id

        with patch("apps.evaluations.evaluators.LlmEvaluator.__init__", return_value=None):
            response = client_with_user.post(
                _edit_url(team, evaluator), _post_data(evaluator, {}, [], llm_provider_model=mismatched_model.id)
            )

        assert response.status_code == 200
        assert "providers, but the selected provider is" in response.content.decode()
        evaluator.refresh_from_db()
        assert evaluator.llm_provider_model_id == original_model_id

    def test_another_teams_provider_is_rejected(self, client_with_user, team, evaluator):
        other_teams_provider = LlmProviderFactory.create()
        original_provider_id = evaluator.llm_provider_id

        with patch("apps.evaluations.evaluators.LlmEvaluator.__init__", return_value=None):
            response = client_with_user.post(
                _edit_url(team, evaluator), _post_data(evaluator, {}, [], llm_provider=other_teams_provider.id)
            )

        assert response.status_code == 200
        assert "not available to this team" in response.content.decode()
        evaluator.refresh_from_db()
        assert evaluator.llm_provider_id == original_provider_id

    def test_an_llm_evaluator_without_a_provider_is_rejected(self, client_with_user, team, evaluator):
        original_provider_id = evaluator.llm_provider_id

        with patch("apps.evaluations.evaluators.LlmEvaluator.__init__", return_value=None):
            response = client_with_user.post(
                _edit_url(team, evaluator), _post_data(evaluator, {}, [], llm_provider="", llm_provider_model="")
            )

        assert response.status_code == 200
        assert "Select an LLM provider for this evaluator" in response.content.decode()
        evaluator.refresh_from_db()
        assert evaluator.llm_provider_id == original_provider_id

    def test_a_python_evaluator_clears_the_provider(self, client_with_user, team, evaluator):
        """No provider is posted for an evaluator type with no LLM, so the FKs are nulled."""
        code = "def main(input, output, context, full_history, generated_response, **kwargs):\n    return {}"
        data = _post_data(evaluator, {}, [], params={"code": code})
        data["type"] = "PythonEvaluator"
        del data["llm_provider"]
        del data["llm_provider_model"]

        response = client_with_user.post(_edit_url(team, evaluator), data)

        assert response.status_code == 302, getattr(response, "context_data", None)
        evaluator.refresh_from_db()
        assert evaluator.llm_provider_id is None
        assert evaluator.llm_provider_model_id is None


class TestPromptVariables:
    """The form's autocomplete and help text are derived from the evaluators' actual kwargs."""

    def test_both_modes_are_shipped_to_the_form(self, client_with_user, team):
        response = client_with_user.get(reverse("evaluations:evaluator_new", args=[team.slug]))

        by_mode = response.context_data["prompt_variables_by_mode"]
        assert set(by_mode) == {"message", "session"}

    @pytest.mark.parametrize("mode", ["message", "session"])
    def test_participant_data_and_session_state_are_offered(self, client_with_user, team, mode):
        """Both are captured on every message, so neither mode should hide them."""
        response = client_with_user.get(reverse("evaluations:evaluator_new", args=[team.slug]))

        variables = response.context_data["prompt_variables_by_mode"][mode]
        assert {"participant_data", "session_state"} <= set(variables["autocomplete"])
        assert "{session_state.[key]}" in variables["hints"]

    def test_session_mode_omits_the_per_message_variables(self, client_with_user, team):
        """A session-mode dataset holds a whole conversation, not one input/output pair."""
        response = client_with_user.get(reverse("evaluations:evaluator_new", args=[team.slug]))

        session_vars = response.context_data["prompt_variables_by_mode"]["session"]["autocomplete"]
        assert not {"input.content", "output.content", "generated_response"} & set(session_vars)

    def test_every_offered_variable_is_actually_interpolated(self, client_with_user, team):
        """A variable the form offers but run() omits raises KeyError, which evaluate_message
        buries in a per-message error result -- nothing else would catch the drift."""
        response = client_with_user.get(reverse("evaluations:evaluator_new", args=[team.slug]))
        by_mode = response.context_data["prompt_variables_by_mode"]
        offered = {name for mode in by_mode for name in by_mode[mode]["autocomplete"]}

        message = EvaluationMessageFactory.create()
        evaluator = evaluators.LlmEvaluator.model_construct(
            prompt=" ".join(f"{{{name}}}" for name in sorted(offered)),
            output_schema={},
        )
        with patch.object(evaluators.LlmEvaluator, "get_chat_model") as get_chat_model:
            get_chat_model.return_value.with_structured_output.return_value.with_retry.return_value.invoke.return_value = (  # noqa: E501
                _StubResult()
            )
            evaluator.run(message, "a response")  # no KeyError => every offered variable is supplied


class _StubResult:
    def model_dump(self):
        return {}
