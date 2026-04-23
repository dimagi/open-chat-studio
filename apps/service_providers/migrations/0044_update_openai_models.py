from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0043_migrate_gemini_3_pro_preview"),
        ("experiments", "0131_drop_llm_provider_columns"),
    ]

    operations = []
