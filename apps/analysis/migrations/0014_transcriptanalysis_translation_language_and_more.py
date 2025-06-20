# Generated by Django 5.1.5 on 2025-06-13 20:17

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analysis', '0013_alter_transcriptanalysis_query_file'),
        ('service_providers', '0028_embeddingprovidermodel'),
    ]

    operations = [
        migrations.AddField(
            model_name='transcriptanalysis',
            name='translation_language',
            field=models.CharField(blank=True, help_text='ISO 639-2/T language code for translation', max_length=3),
        ),
        migrations.AddField(
            model_name='transcriptanalysis',
            name='translation_llm_provider',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='translation_analyses', to='service_providers.llmprovider', verbose_name='Translation LLM Provider'),
        ),
        migrations.AddField(
            model_name='transcriptanalysis',
            name='translation_llm_provider_model',
            field=models.ForeignKey(blank=True, help_text='The LLM model to use for translation', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='translation_analyses', to='service_providers.llmprovidermodel', verbose_name='Translation LLM Model'),
        ),
    ]
