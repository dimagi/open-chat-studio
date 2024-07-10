# Generated by Django 4.2.11 on 2024-06-19 15:27

from django.db import migrations

def create_celery_schedules(apps, schema_editor):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    schedule, _ = CrontabSchedule.objects.get_or_create(minute='*', hour="*", day_of_week="*", month_of_year="*")
    PeriodicTask.objects.update_or_create(
        name="apps.chat.tasks.no_activity_pings",
        defaults={
            "crontab": schedule,
            "task": "apps.chat.tasks.no_activity_pings",
            "expire_seconds": 60
        }
    )


def delete_celery_schedules(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    PeriodicTask.objects.filter(name="apps.chat.tasks.no_activity_pings").delete()

class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0013_remove_chat_user'),
    ]

    operations = [
        migrations.RunPython(delete_celery_schedules, create_celery_schedules)
    ]