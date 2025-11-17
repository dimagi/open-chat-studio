import logging
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('service_providers', '0025_alter_llmprovider_type_alter_llmprovidermodel_type'),
    ]
    operations = [
        migrations.AlterField(
            model_name='llmprovider',
            name='type',
            field=models.CharField(choices=[
                ('openai', 'OpenAI'),
                ('azure', 'Azure OpenAI'),
                ('anthropic', 'Anthropic'),
                ('groq', 'Groq'),
                ('perplexity', 'Perplexity'),
                ('deepseek', 'DeepSeek'),
                ('google', 'Google Gemini')
            ], max_length=255),
        ),
        migrations.AlterField(
            model_name='llmprovidermodel',
            name='type',
            field=models.CharField(choices=[
                ('openai', 'OpenAI'),
                ('azure', 'Azure OpenAI'),
                ('anthropic', 'Anthropic'),
                ('groq', 'Groq'),
                ('perplexity', 'Perplexity'),
                ('deepseek', 'DeepSeek'),
                ('google', 'Google Gemini')
            ], max_length=255),
        ),
    ]
