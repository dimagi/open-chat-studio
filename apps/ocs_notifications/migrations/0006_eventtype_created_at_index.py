from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ocs_notifications", "0005_delete_notification"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="eventtype",
            index=models.Index(fields=["created_at"], name="eventtype_created_at_idx"),
        ),
    ]
