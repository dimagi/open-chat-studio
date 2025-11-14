import logging
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('service_providers', '0029_add_o3_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='llmprovidermodel',
            name='deprecated',
            field=models.BooleanField(default=False),
        ),
    ]
