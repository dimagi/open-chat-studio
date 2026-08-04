import importlib

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from apps.evaluations.models import Evaluator
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory

_migration = importlib.import_module("apps.evaluations.migrations.0021_strip_evaluator_llm_provider_params")
strip_llm_provider_params = _migration.strip_llm_provider_params
restore_llm_provider_params = _migration.restore_llm_provider_params


class FakeSchemaEditor:
    connection = connection


@pytest.fixture(autouse=True)
def _requires_migrations(requires_migrations):
    """Every test here loads historical state via the migration graph."""


def _run(migration_function):
    """Run against the app state the migration actually receives, not the live registry."""
    state = MigrationLoader(None).project_state([("evaluations", "0020_evaluationrun_finalized_at")])
    migration_function(state.apps, FakeSchemaEditor())


def _make_pre_migration_evaluator(params: dict, **kwargs):
    """An evaluator as it looked between 0019 and 0020: ids on the FKs *and* in params."""
    evaluator = EvaluatorFactory.create(params=params, **kwargs)
    Evaluator.objects.filter(pk=evaluator.pk).update(
        params=params
        | {"llm_provider_id": evaluator.llm_provider_id, "llm_provider_model_id": evaluator.llm_provider_model_id}
    )
    return evaluator


@pytest.mark.django_db()
def test_strips_the_ids_and_keeps_the_rest_of_params():
    evaluator = _make_pre_migration_evaluator({"prompt": "p", "llm_temperature": 0.3})

    _run(strip_llm_provider_params)

    evaluator.refresh_from_db()
    assert evaluator.params == {"prompt": "p", "llm_temperature": 0.3}
    assert evaluator.llm_provider_id is not None
    assert evaluator.llm_provider_model_id is not None


@pytest.mark.django_db()
def test_a_params_id_that_disagrees_with_the_fk_is_discarded():
    """The FK is what the runtime has resolved since 0019, so the params copy just goes."""
    evaluator = _make_pre_migration_evaluator({"prompt": "p"})
    other_provider = LlmProviderFactory.create(team=evaluator.team)
    Evaluator.objects.filter(pk=evaluator.pk).update(
        params={"prompt": "p", "llm_provider_id": other_provider.id, "llm_provider_model_id": 0}
    )
    original_provider_id = evaluator.llm_provider_id

    _run(strip_llm_provider_params)

    evaluator.refresh_from_db()
    assert evaluator.params == {"prompt": "p"}
    assert evaluator.llm_provider_id == original_provider_id


@pytest.mark.django_db()
def test_evaluators_without_provider_params_are_untouched():
    evaluator = EvaluatorFactory.create(
        type="PythonEvaluator",
        llm_provider=None,
        llm_provider_model=None,
        params={"code": "def main(**kwargs): pass"},
    )

    _run(strip_llm_provider_params)

    evaluator.refresh_from_db()
    assert evaluator.params == {"code": "def main(**kwargs): pass"}


@pytest.mark.django_db()
def test_reverse_writes_the_fk_ids_back_into_params():
    provider = LlmProviderFactory.create()
    provider_model = LlmProviderModelFactory.create(team=provider.team)
    evaluator = EvaluatorFactory.create(
        team=provider.team, llm_provider=provider, llm_provider_model=provider_model, params={"prompt": "p"}
    )

    _run(restore_llm_provider_params)

    evaluator.refresh_from_db()
    assert evaluator.params == {
        "prompt": "p",
        "llm_provider_id": provider.id,
        "llm_provider_model_id": provider_model.id,
    }
