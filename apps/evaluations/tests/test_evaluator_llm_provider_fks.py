"""The Evaluator.llm_provider / llm_provider_model FK columns, the only record of the selection."""

import pytest

from apps.evaluations.exceptions import EvaluationRunException
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


@pytest.fixture()
def provider(db):
    return LlmProviderFactory.create()


@pytest.fixture()
def provider_model(provider):
    return LlmProviderModelFactory.create(team=provider.team)


@pytest.fixture()
def evaluator(provider, provider_model):
    return EvaluatorFactory.create(
        team=provider.team, llm_provider=provider, llm_provider_model=provider_model, params=_params()
    )


def _params() -> dict:
    return {
        "prompt": "evaluate {input.content}",
        "output_schema": {"score": {"type": "int", "description": "score"}},
    }


@pytest.mark.django_db()
class TestProviderReference:
    def test_params_carries_no_provider_ids(self, evaluator, provider, provider_model):
        evaluator.refresh_from_db()

        assert evaluator.params == _params()
        assert evaluator.llm_provider_id == provider.id
        assert evaluator.llm_provider_model_id == provider_model.id

    def test_provider_deletion_nulls_the_fk(self, evaluator, provider, provider_model):
        provider.delete()

        evaluator.refresh_from_db()
        assert evaluator.llm_provider_id is None
        assert evaluator.llm_provider_model_id == provider_model.id


@pytest.mark.django_db()
class TestRuntimeResolution:
    def test_the_fk_ids_are_injected_into_the_params(self, evaluator, provider, provider_model):
        """``LLMResponseMixin`` takes the ids as fields, so they are supplied from the FKs."""
        assert evaluator.get_evaluator_params() == _params() | {
            "llm_provider_id": provider.id,
            "llm_provider_model_id": provider_model.id,
        }

    def test_a_legacy_params_copy_is_overridden_by_the_fks(self, provider, provider_model):
        """Rows saved before the ids left ``params`` still carry them; the FK is what runs."""
        other_provider = LlmProviderFactory.create(team=provider.team)
        evaluator = EvaluatorFactory.create(
            team=provider.team,
            llm_provider=provider,
            llm_provider_model=provider_model,
            params=_params() | {"llm_provider_id": other_provider.id, "llm_provider_model_id": 0},
        )

        assert evaluator.get_evaluator_params()["llm_provider_id"] == provider.id
        assert evaluator.get_evaluator_params()["llm_provider_model_id"] == provider_model.id

    def test_missing_provider_raises_a_useful_error(self, evaluator, provider):
        provider.delete()
        evaluator.refresh_from_db()

        with pytest.raises(EvaluationRunException, match="no LLM provider configured"):
            evaluator.get_evaluator_params()

    def test_python_evaluator_params_pass_through(self):
        params = {"code": "def main(**kwargs): pass"}
        evaluator = EvaluatorFactory.create(
            type="PythonEvaluator", llm_provider=None, llm_provider_model=None, params=params
        )

        assert evaluator.get_evaluator_params() == params
