"""Drop unused waffle tables that cause FK constraint errors during test teardown.

Since we use a custom flag model (teams.Flag via WAFFLE_FLAG_MODEL), the default
waffle_flag_groups and waffle_flag_users tables are unused but still have FK
references to auth_group. This causes PostgreSQL to reject TRUNCATE commands
during test teardown:

    django.db.utils.NotSupportedError: cannot truncate a table referenced in
    a foreign key constraint
    DETAIL: Table "waffle_flag_groups" references "auth_group".

Dropping these unused tables allows proper test cleanup without needing the
`available_apps` workaround in django_db_with_data.

See: https://github.com/django-waffle/django-waffle/issues/317
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("teams", "0007_create_commcare_connect_flag"),
        ("waffle", "0004_update_everyone_nullbooleanfield"),
    ]

    operations = [
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS waffle_flag_groups CASCADE",
            reverse_sql="CREATE TABLE waffle_flag_groups (id SERIAL PRIMARY KEY, flag_id INTEGER, group_id INTEGER)",
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS waffle_flag_users CASCADE",
            reverse_sql="CREATE TABLE waffle_flag_users (id SERIAL PRIMARY KEY, flag_id INTEGER, user_id INTEGER)",
        ),
    ]
