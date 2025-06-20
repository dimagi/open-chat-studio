# Generated by Django 5.1.5 on 2025-05-28 06:37

from django.db import migrations

def make_dict_null(apps, schema_editor):
    apps.get_model('documents', 'CollectionFile').objects.filter(metadata={}).update(metadata=None)

class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0006_alter_collectionfile_metadata'),
    ]

    operations = [
        migrations.RunPython(make_dict_null, migrations.RunPython.noop),
    ]
