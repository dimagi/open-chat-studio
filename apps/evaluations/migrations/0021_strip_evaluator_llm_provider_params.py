from django.db import migrations

BATCH_SIZE = 500
PARAM_KEYS = ("llm_provider_id", "llm_provider_model_id")


def strip_llm_provider_params(apps, schema_editor):
    """Drop the copy of the provider ids from ``Evaluator.params``, leaving the FKs as the record.

    Where the two disagree the FK wins — it is what the runtime has resolved since 0019, so
    the params copy is discarded, not reconciled.
    """
    Evaluator = apps.get_model("evaluations", "Evaluator")

    to_update = []
    for evaluator in Evaluator.objects.all().iterator(chunk_size=BATCH_SIZE):
        params = evaluator.params or {}
        if not any(key in params for key in PARAM_KEYS):
            continue
        evaluator.params = {key: value for key, value in params.items() if key not in PARAM_KEYS}
        to_update.append(evaluator)
        if len(to_update) >= BATCH_SIZE:
            Evaluator.objects.bulk_update(to_update, ["params"])
            to_update.clear()

    if to_update:
        Evaluator.objects.bulk_update(to_update, ["params"])


def restore_llm_provider_params(apps, schema_editor):
    """Write the FK ids back into ``params`` so the pre-#3995 form can read them again."""
    Evaluator = apps.get_model("evaluations", "Evaluator")

    to_update = []
    for evaluator in Evaluator.objects.exclude(llm_provider=None, llm_provider_model=None).iterator(
        chunk_size=BATCH_SIZE
    ):
        evaluator.params = (evaluator.params or {}) | {
            "llm_provider_id": evaluator.llm_provider_id,
            "llm_provider_model_id": evaluator.llm_provider_model_id,
        }
        to_update.append(evaluator)
        if len(to_update) >= BATCH_SIZE:
            Evaluator.objects.bulk_update(to_update, ["params"])
            to_update.clear()

    if to_update:
        Evaluator.objects.bulk_update(to_update, ["params"])


class Migration(migrations.Migration):
    dependencies = [("evaluations", "0020_evaluationrun_finalized_at")]

    operations = [migrations.RunPython(strip_llm_provider_params, restore_llm_provider_params)]
