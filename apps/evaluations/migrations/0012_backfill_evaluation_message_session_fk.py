from django.db import migrations


def backfill_session_fk(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            UPDATE evaluations_evaluationmessage em
            SET session_id = es.id
            FROM experiments_experimentsession es
            WHERE em.session_id IS NULL
              AND em.metadata->>'session_id' IS NOT NULL
              AND em.metadata->>'session_id' != ''
              AND es.external_id = em.metadata->>'session_id'
        """)


class Migration(migrations.Migration):
    dependencies = [
        ("evaluations", "0011_add_session_fk_to_evaluation_message"),
        ("experiments", "0132_syntheticvoice_external_id_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_session_fk, migrations.RunPython.noop),
    ]
