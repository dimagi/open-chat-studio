from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0087_alter_authprovider_type"),
        ("service_providers", "0097_auto_model_sync_20261027"),
    ]

    operations = [
        migrations.AddField(
            model_name="authprovider",
            name="extra_data",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
