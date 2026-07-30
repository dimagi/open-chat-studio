from django.db import migrations

from apps.utils.fields import as_int

BATCH_SIZE = 500


def backfill_evaluator_llm_provider_fks(apps, schema_editor):
    """Point the new FK columns at the provider ids already stored in ``Evaluator.params``.

    Ids that no longer resolve are left null rather than written through: providers and
    provider models can be (and have been) deleted while an evaluator still names them in
    params, and a dangling id would fail the FK constraint. Those evaluators were already
    broken at runtime — this just stops pretending otherwise.
    """
    Evaluator = apps.get_model("evaluations", "Evaluator")
    LlmProvider = apps.get_model("service_providers", "LlmProvider")
    LlmProviderModel = apps.get_model("service_providers", "LlmProviderModel")

    provider_ids = set(LlmProvider.objects.values_list("id", flat=True))
    provider_model_ids = set(LlmProviderModel.objects.values_list("id", flat=True))

    to_update = []
    for evaluator in Evaluator.objects.all().iterator(chunk_size=BATCH_SIZE):
        params = evaluator.params or {}
        provider_id = as_int(params.get("llm_provider_id"))
        provider_model_id = as_int(params.get("llm_provider_model_id"))
        evaluator.llm_provider_id = provider_id if provider_id in provider_ids else None
        evaluator.llm_provider_model_id = provider_model_id if provider_model_id in provider_model_ids else None
        if evaluator.llm_provider_id is None and evaluator.llm_provider_model_id is None:
            continue
        to_update.append(evaluator)
        if len(to_update) >= BATCH_SIZE:
            Evaluator.objects.bulk_update(to_update, ["llm_provider_id", "llm_provider_model_id"])
            to_update.clear()

    if to_update:
        Evaluator.objects.bulk_update(to_update, ["llm_provider_id", "llm_provider_model_id"])


class Migration(migrations.Migration):
    dependencies = [("evaluations", "0018_evaluator_llm_provider_fks")]

    operations = [migrations.RunPython(backfill_evaluator_llm_provider_fks, migrations.RunPython.noop)]
