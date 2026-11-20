from django.db import migrations, models

import apps.files.models


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0019_filechunkembedding_metadata_index"),
    ]

    operations = [
        migrations.AlterField(
            model_name="file",
            name="file",
            field=models.FileField(max_length=255, upload_to=apps.files.models.file_upload_to),
        ),
    ]
