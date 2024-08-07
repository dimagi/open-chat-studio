# Generated by Django 4.2.7 on 2024-02-27 09:01

from django.db import migrations


def update_periodic_task(apps, schema_editor):
    """Change from 10s interval to 60s interval for enqueue_timed_out_events task."""
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    task = PeriodicTask.objects.filter(
        name="events.tasks.enqueue_timed_out_events",
        task="apps.events.tasks.enqueue_timed_out_events",
    ).first()
    if not task:
        return

    interval, _ = IntervalSchedule.objects.get_or_create(every=60, period="seconds")
    task.interval = interval
    task.save()

    # delete old interval if no tasks are using it
    try:
        old_interval = IntervalSchedule.objects.get(every=10, period="seconds")
    except IntervalSchedule.DoesNotExist:
        return

    if not old_interval.periodictask_set.count():
        old_interval.delete()


class Migration(migrations.Migration):
    dependencies = [
        ('events', '0010_alter_eventaction_action_type')
    ]

    operations = [migrations.RunPython(update_periodic_task, migrations.RunPython.noop)]
