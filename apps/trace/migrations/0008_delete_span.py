from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("trace", "0007_trace_error"),
    ]

    operations = [
        migrations.DeleteModel(
            name="Span",
        ),
    ]
