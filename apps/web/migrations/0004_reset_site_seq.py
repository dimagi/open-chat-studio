from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ("web", "0003_rename_flags"),
    ]

    operations = [
        migrations.RunSQL(
            sql="SELECT setval(pg_get_serial_sequence('django_site', 'id'), (SELECT MAX(id) FROM django_site));",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
