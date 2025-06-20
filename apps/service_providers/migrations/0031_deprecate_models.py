from django.db import migrations

def deprecate_specific_models(apps, schema_editor):
    LlmProviderModel = apps.get_model("service_providers", "LlmProviderModel")

    models_to_deprecate = [
        ("gpt-35-turbo-16k", "azure"),
        ("gpt-4", "azure"),
        ("gpt-4-32k", "azure"),
        ("llama-3.1-sonar-small-128k-online", "perplexity"),
        ("llama-3.1-sonar-large-128k-online", "perplexity"),
        ("llama-3.1-sonar-huge-128k-online", "perplexity"),
        ("claude-2.0", "anthropic"),
        ("claude-2.1", "anthropic"),
        ("claude-instant-1.2", "anthropic"),
        ("gpt-4-0613", "openai"),
        ("gpt-4-1106-preview", "openai"),
        ("gpt-4-0125-preview", "openai"),
        ("llama-guard-3-8b", "groq"),
        ("llama-3.2-1b-preview", "groq"),
        ("llama-3.2-3b-preview", "groq"),
        ("llama-3.2-11b-vision-preview", "groq"),
        ("llama-3.2-90b-vision-preview", "groq"),
        ("llama-3.1-70b-versatile", "groq"),
        ("llama3-groq-70b-8192-tool-use-preview", "groq"),
        ("llama3-groq-8b-8192-tool-use-preview", "groq"),
        ("gemma-7b-it", "groq"),
    ]

    for name, type_ in models_to_deprecate:
        updated = LlmProviderModel.objects.filter(name=name, type=type_).update(deprecated=True)

class Migration(migrations.Migration):

    dependencies = [
        ("service_providers", "0030_llmprovidermodel_deprecated"),
    ]

    operations = [
        migrations.RunPython(deprecate_specific_models),
    ]
