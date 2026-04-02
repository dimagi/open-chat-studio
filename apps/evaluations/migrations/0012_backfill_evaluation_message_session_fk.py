from django.db import migrations


def backfill_session_fk(apps, schema_editor):
    EvaluationMessage = apps.get_model("evaluations", "EvaluationMessage")
    ExperimentSession = apps.get_model("experiments", "ExperimentSession")

    # Collect all external IDs referenced in metadata
    external_ids = set()
    for metadata in EvaluationMessage.objects.exclude(metadata__session_id__isnull=True).values_list(
        "metadata", flat=True
    ):
        if isinstance(metadata, dict):
            sid = metadata.get("session_id")
            if sid:
                external_ids.add(str(sid))

    if not external_ids:
        return

    session_map = {
        str(s.external_id): s.pk
        for s in ExperimentSession.objects.filter(external_id__in=external_ids)
    }

    batch = []
    for msg in EvaluationMessage.objects.filter(session__isnull=True).exclude(metadata__session_id__isnull=True):
        sid = msg.metadata.get("session_id") if isinstance(msg.metadata, dict) else None
        if sid and str(sid) in session_map:
            msg.session_id = session_map[str(sid)]
            batch.append(msg)
        if len(batch) >= 500:
            EvaluationMessage.objects.bulk_update(batch, ["session_id"])
            batch = []

    if batch:
        EvaluationMessage.objects.bulk_update(batch, ["session_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("evaluations", "0011_add_session_fk_to_evaluation_message"),
        ("experiments", "0132_syntheticvoice_external_id_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_session_fk, migrations.RunPython.noop),
    ]
