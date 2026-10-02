from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pipelines", "0032_alter_node_position_x_alter_node_position_y"),
    ]

    operations = [
        migrations.AlterField(
            model_name="node",
            name="position_x",
            field=models.FloatField(default=0),
        ),
        migrations.AlterField(
            model_name="node",
            name="position_y",
            field=models.FloatField(default=0),
        ),
    ]
