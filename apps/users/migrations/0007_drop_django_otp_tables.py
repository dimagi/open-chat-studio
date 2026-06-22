from django.db import migrations

# Tables left behind by django-allauth-2fa / django-otp after the allauth.mfa
# migration (see 0005_migrate_2fa_data). The apps are no longer installed, so
# these tables are orphaned and can be dropped.
DROP_SQL = """
DROP TABLE IF EXISTS otp_static_statictoken CASCADE;
DROP TABLE IF EXISTS otp_static_staticdevice CASCADE;
DROP TABLE IF EXISTS otp_totp_totpdevice CASCADE;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0006_alter_customuser_options_alter_customuser_managers"),
    ]

    operations = [
        migrations.RunSQL(DROP_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
