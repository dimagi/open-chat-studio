from django.db import migrations


def backfill_evaluation_mode(apps, schema_editor):
    EvaluationDataset = apps.get_model('evaluations', 'EvaluationDataset')
    Evaluator = apps.get_model('evaluations', 'Evaluator')
    EvaluationDataset.objects.filter(evaluation_mode__isnull=True).update(evaluation_mode='message')
    Evaluator.objects.filter(evaluation_mode__isnull=True).update(evaluation_mode='message')


class Migration(migrations.Migration):

    dependencies = [
        ('evaluations', '0011_add_evaluation_mode'),
    ]

    operations = [
        migrations.RunPython(backfill_evaluation_mode, migrations.RunPython.noop),
    ]
