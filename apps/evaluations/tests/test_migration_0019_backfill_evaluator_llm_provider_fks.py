import importlib

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from apps.evaluations.models import Evaluator
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory

_migration = importlib.import_module("apps.evaluations.migrations.0019_backfill_evaluator_llm_provider_fks")
backfill_evaluator_llm_provider_fks = _migration.backfill_evaluator_llm_provider_fks


class FakeSchemaEditor:
    connection = connection


def _run_migration():
    """Run the backfill against the app state it actually receives at migration time.

    Handing it the live registry instead would let it see model methods and fields that
    don't exist in the historical state — the class of bug commit 72686ae6d fixed in
    ``_repoint_evaluators``.
    """
    state = MigrationLoader(None).project_state([("evaluations", "0018_evaluator_llm_provider_fks")])
    backfill_evaluator_llm_provider_fks(state.apps, FakeSchemaEditor())


def _make_pre_migration_evaluator(**kwargs):
    """An evaluator as it looked before the FK columns existed: ids in params only."""
    evaluator = EvaluatorFactory.create(**kwargs)
    Evaluator.objects.filter(pk=evaluator.pk).update(llm_provider=None, llm_provider_model=None)
    return evaluator


@pytest.mark.django_db()
def test_backfills_fks_from_params():
    provider = LlmProviderFactory.create()
    provider_model = LlmProviderModelFactory.create(team=provider.team)
    evaluator = _make_pre_migration_evaluator(
        team=provider.team,
        params={"llm_provider_id": provider.id, "llm_provider_model_id": str(provider_model.id)},
    )

    _run_migration()

    evaluator.refresh_from_db()
    assert evaluator.llm_provider_id == provider.id
    assert evaluator.llm_provider_model_id == provider_model.id


@pytest.mark.django_db()
def test_dangling_ids_are_left_null():
    """A provider deleted before the migration ran must not become an FK violation."""
    provider = LlmProviderFactory.create()
    evaluator = _make_pre_migration_evaluator(
        team=provider.team,
        params={"llm_provider_id": provider.id + 1000, "llm_provider_model_id": "not-an-id"},
    )

    _run_migration()

    evaluator.refresh_from_db()
    assert evaluator.llm_provider_id is None
    assert evaluator.llm_provider_model_id is None


@pytest.mark.django_db()
def test_evaluators_without_provider_params_are_untouched():
    evaluator = _make_pre_migration_evaluator(type="PythonEvaluator", params={"code": "def main(**kwargs): pass"})

    _run_migration()

    evaluator.refresh_from_db()
    assert evaluator.llm_provider_id is None
    assert evaluator.llm_provider_model_id is None
