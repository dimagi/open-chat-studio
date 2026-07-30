"""The Evaluator.llm_provider / llm_provider_model FK columns and how they track params."""

import pytest

from apps.evaluations.exceptions import EvaluationRunException
from apps.evaluations.models import Evaluator
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


@pytest.fixture()
def provider(db):
    return LlmProviderFactory.create()


@pytest.fixture()
def provider_model(provider):
    return LlmProviderModelFactory.create(team=provider.team)


def _params(provider_id, provider_model_id) -> dict:
    return {
        "llm_provider_id": provider_id,
        "llm_provider_model_id": provider_model_id,
        "prompt": "evaluate {input.content}",
        "output_schema": {"score": {"type": "int", "description": "score"}},
    }


@pytest.mark.django_db()
class TestFkSync:
    @pytest.mark.parametrize("cast", [pytest.param(int, id="int-in-params"), pytest.param(str, id="str-in-params")])
    def test_fks_populated_from_params_on_create(self, provider, provider_model, cast):
        evaluator = EvaluatorFactory.create(
            team=provider.team, params=_params(cast(provider.id), cast(provider_model.id))
        )

        evaluator.refresh_from_db()
        assert evaluator.llm_provider_id == provider.id
        assert evaluator.llm_provider_model_id == provider_model.id

    def test_fks_follow_params_on_update(self, provider, provider_model):
        evaluator = EvaluatorFactory.create(team=provider.team, params=_params(provider.id, provider_model.id))
        other_provider = LlmProviderFactory.create(team=provider.team)

        evaluator.params["llm_provider_id"] = other_provider.id
        evaluator.save(update_fields=["params"])

        evaluator.refresh_from_db()
        assert evaluator.llm_provider_id == other_provider.id

    def test_dangling_ids_become_null(self, provider, provider_model):
        """A deleted provider leaves a stale id in params; the FK must not resurrect it.

        The stale id stays in ``params`` on purpose — the form still edits it, and #3995
        removes the copy from ``params`` altogether.
        """
        evaluator = EvaluatorFactory.create(
            team=provider.team, params=_params(provider.id + 1000, provider_model.id + 1000)
        )

        evaluator.refresh_from_db()
        assert evaluator.llm_provider_id is None
        assert evaluator.llm_provider_model_id is None

    def test_python_evaluator_has_no_provider(self):
        evaluator = EvaluatorFactory.create(type="PythonEvaluator", params={"code": "def main(**kwargs): pass"})

        evaluator.refresh_from_db()
        assert evaluator.llm_provider_id is None
        assert evaluator.llm_provider_model_id is None

    def test_provider_deletion_nulls_the_fk(self, provider, provider_model):
        evaluator = EvaluatorFactory.create(team=provider.team, params=_params(provider.id, provider_model.id))

        provider.delete()

        evaluator.refresh_from_db()
        assert evaluator.llm_provider_id is None
        assert evaluator.llm_provider_model_id == provider_model.id

    def test_unrelated_update_does_not_touch_the_fks(self, provider, provider_model):
        """A targeted save that doesn't include params leaves the FK columns alone."""
        evaluator = EvaluatorFactory.create(team=provider.team, params=_params(provider.id, provider_model.id))
        other_model = LlmProviderModelFactory.create(team=provider.team)
        Evaluator.objects.filter(pk=evaluator.pk).update(llm_provider_model=other_model)

        evaluator.refresh_from_db()
        evaluator.name = "renamed"
        evaluator.save(update_fields=["name"])

        evaluator.refresh_from_db()
        assert evaluator.name == "renamed"
        assert evaluator.llm_provider_model_id == other_model.id

    def test_an_unchanged_provider_costs_no_existence_queries(
        self, provider, provider_model, django_assert_num_queries
    ):
        """Re-deriving an id the FK column already holds needs no validation.

        A non-null FK is guaranteed to resolve by the constraint, and SET_NULL would have
        nulled it if the row had gone away.
        """
        evaluator = EvaluatorFactory.create(team=provider.team, params=_params(provider.id, provider_model.id))
        evaluator.refresh_from_db()

        with django_assert_num_queries(1):  # the UPDATE only
            evaluator.save(update_fields=["params"])

    def test_a_changed_provider_is_still_validated(self, provider, provider_model):
        """The check is skipped only when the value matches; a new id is still verified."""
        evaluator = EvaluatorFactory.create(team=provider.team, params=_params(provider.id, provider_model.id))
        evaluator.params["llm_provider_id"] = provider.id + 1000
        evaluator.save(update_fields=["params"])

        evaluator.refresh_from_db()
        assert evaluator.llm_provider_id is None

    def test_set_llm_provider_model_id_moves_params_and_fk(self, provider, provider_model):
        evaluator = EvaluatorFactory.create(team=provider.team, params=_params(provider.id, provider_model.id))
        replacement = LlmProviderModelFactory.create(team=provider.team)

        evaluator.set_llm_provider_model_id(replacement.id)

        evaluator.refresh_from_db()
        assert evaluator.llm_provider_model_id == replacement.id
        assert evaluator.params["llm_provider_model_id"] == replacement.id


@pytest.mark.django_db()
class TestRuntimeResolution:
    def test_fk_wins_over_a_stale_params_id(self, provider, provider_model):
        """The FK is the reference; params only feeds the form UI."""
        evaluator = EvaluatorFactory.create(team=provider.team, params=_params(provider.id, provider_model.id))
        other_provider = LlmProviderFactory.create(team=provider.team)
        Evaluator.objects.filter(pk=evaluator.pk).update(llm_provider=other_provider)
        evaluator.refresh_from_db()

        assert evaluator.get_evaluator_params()["llm_provider_id"] == other_provider.id

    def test_missing_provider_raises_a_useful_error(self, provider, provider_model):
        evaluator = EvaluatorFactory.create(team=provider.team, params=_params(provider.id, provider_model.id))
        provider.delete()
        evaluator.refresh_from_db()

        with pytest.raises(EvaluationRunException, match="no LLM provider configured"):
            evaluator.get_evaluator_params()

    def test_python_evaluator_params_pass_through(self):
        params = {"code": "def main(**kwargs): pass"}
        evaluator = EvaluatorFactory.create(type="PythonEvaluator", params=params)

        assert evaluator.get_evaluator_params() == params
