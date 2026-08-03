from django.db import migrations

DEPRECATED_DEEPSEEK_MODELS = ("deepseek-chat", "deepseek-reasoner")


def deprecate_deepseek_models(apps, schema_editor):
    LlmProviderModel = apps.get_model("service_providers", "LlmProviderModel")
    LlmProviderModel.objects.filter(type="deepseek", name__in=DEPRECATED_DEEPSEEK_MODELS).update(deprecated=True)


def undeprecate_deepseek_models(apps, schema_editor):
    LlmProviderModel = apps.get_model("service_providers", "LlmProviderModel")
    LlmProviderModel.objects.filter(type="deepseek", name__in=DEPRECATED_DEEPSEEK_MODELS).update(deprecated=False)


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0069_add_deepseek_v4_flash"),
    ]

    operations = [
        # deepseek-chat and deepseek-reasoner have been removed from DEFAULT_LLM_PROVIDER_MODELS
        # (deepseek-v4-flash is now the default). Mark any existing global/custom rows deprecated
        # rather than deleting them so sessions/pipelines still referencing them keep working.
        migrations.RunPython(deprecate_deepseek_models, undeprecate_deepseek_models),
    ]
