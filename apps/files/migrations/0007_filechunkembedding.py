# Generated by Django 5.1.5 on 2025-05-28 15:11

import apps.utils.models
import django.db.models.deletion
import pgvector.django.indexes
import pgvector.django.vector
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0008_add_pg_vector_extension'),
        ('files', '0006_remove_file_schema_file_metadata'),
        ('teams', '0007_create_commcare_connect_flag'),
    ]

    operations = [
        migrations.CreateModel(
            name='FileChunkEmbedding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('chunk_number', models.PositiveIntegerField()),
                ('text', models.TextField()),
                ('page_number', models.PositiveIntegerField(blank=True)),
                ('embedding', pgvector.django.vector.VectorField(dimensions=1024)),
                ('collection', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='documents.collection')),
                ('file', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='files.file')),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='teams.team', verbose_name='Team')),
            ],
            options={
                'indexes': [pgvector.django.indexes.HnswIndex(fields=['embedding'], name='embedding_index', opclasses=['vector_cosine_ops'])],
            },
            bases=(models.Model, apps.utils.models.VersioningMixin),
        ),
    ]
