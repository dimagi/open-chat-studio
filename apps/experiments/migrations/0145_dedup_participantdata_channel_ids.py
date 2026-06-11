"""
Data migration to remove duplicate ParticipantData rows that share the same
(participant, commcare_connect_channel_id) pair.

Root cause: the CommCare Connect create_channel API is idempotent and returns
the same channel_id when called with the same (connect_id, channel_source).
A participant enrolled in multiple experiments that share the same bot would
accumulate multiple ParticipantData rows with identical channel_ids, causing
MultipleObjectsReturned in the generate_key view.

Strategy: for each group of duplicates, keep the row with an encryption_key
(or the highest pk if none have one). Copy that row's encryption_key to
remaining rows and clear the commcare_connect_channel_id from the extras so
that generate_key can no longer match them.
"""

from django.db import migrations


def dedup_channel_ids(apps, schema_editor):
    ParticipantData = apps.get_model("experiments", "ParticipantData")

    # Find all participant ids that have more than one ParticipantData row
    # sharing the same commcare_connect_channel_id.
    seen = {}  # (participant_id, channel_id) -> canonical ParticipantData pk
    duplicates = []  # pks to fix

    rows = (
        ParticipantData.objects.filter(system_metadata__has_key="commcare_connect_channel_id")
        .values("id", "participant_id", "system_metadata", "encryption_key")
        .order_by("participant_id", "-encryption_key", "-id")  # prefer rows with an encryption_key
    )

    for row in rows:
        channel_id = row["system_metadata"].get("commcare_connect_channel_id")
        if not channel_id:
            continue
        key = (row["participant_id"], channel_id)
        if key not in seen:
            seen[key] = row["id"]
        else:
            duplicates.append(row["id"])

    if not duplicates:
        return

    for pk in duplicates:
        obj = ParticipantData.objects.get(pk=pk)
        channel_id = obj.system_metadata.get("commcare_connect_channel_id")
        canonical_pk = seen.get((obj.participant_id, channel_id))
        if canonical_pk:
            canonical = ParticipantData.objects.get(pk=canonical_pk)
            # Copy encryption key from canonical if available and missing here
            if canonical.encryption_key and not obj.encryption_key:
                obj.encryption_key = canonical.encryption_key
        # Remove the duplicate channel_id to avoid future MultipleObjectsReturned
        obj.system_metadata.pop("commcare_connect_channel_id", None)
        obj.save(update_fields=["system_metadata", "encryption_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("experiments", "0144_remove_experiment_survey_fields_state"),
    ]

    operations = [
        migrations.RunPython(dedup_channel_ids, migrations.RunPython.noop),
    ]