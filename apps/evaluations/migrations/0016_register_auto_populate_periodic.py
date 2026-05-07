from django.db import migrations

# The periodic task is registered via SCHEDULED_TASKS in config/settings.py and managed
# by the setup_periodic_tasks management command (called post-migrate).  This migration
# exists solely to record the dependency on the evaluations schema migration that
# introduced DatasetAutoPopulationRule so that deployment ordering is enforced.


class Migration(migrations.Migration):

    dependencies = [
        ("django_celery_beat", "0019_alter_periodictasks_options"),
        ("evaluations", "0015_auto_populate_schema"),
    ]

    operations = []
