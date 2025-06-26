from datetime import timedelta

from celery import schedules
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django_celery_beat.models import (
    CrontabSchedule,
    IntervalSchedule,
    PeriodicTask,
)


# from https://github.com/celery/django-celery-beat/blob/master/django_celery_beat/management/commands/celery_beat.py
class Command(BaseCommand):
    """
    Command to create, update and remove celery periodic tasks based on the SCHEDULED_TASKS setting
    """

    help = "Create, update and remove celery periodic tasks based on the SCHEDULED_TASKS setting"

    def handle(self, *args, **options):
        self.stdout.write("Setting up periodic tasks...")
        if hasattr(settings, "SCHEDULED_TASKS"):
            self.setup_periodic_tasks(settings.SCHEDULED_TASKS)
            self.stdout.write(self.style.SUCCESS("Periodic tasks are set up"))
        else:
            self.stdout.write(self.style.WARNING("No SCHEDULED_TASKS setting found"))

    @transaction.atomic
    def setup_periodic_tasks(self, scheduled_tasks):
        self.stdout.write("Updating periodic tasks from settings.SCHEDULED_TASKS")
        tasks = list(scheduled_tasks.keys())

        # remove tasks that are not in the settings anymore
        PeriodicTask.objects.exclude(name__in=tasks).delete()

        for name, config in scheduled_tasks.items():
            self.stdout.write(f"Updating periodic task {name}")

            schedule = config["schedule"]
            if isinstance(schedule, int):
                schedule, _ = IntervalSchedule.objects.get_or_create(every=schedule, period=IntervalSchedule.SECONDS)
            elif isinstance(schedule, timedelta):
                schedule, _ = IntervalSchedule.objects.get_or_create(
                    every=schedule.total_seconds(), period=IntervalSchedule.SECONDS
                )
            elif isinstance(schedule, schedules.crontab):
                schedule, _ = CrontabSchedule.objects.get_or_create(
                    minute=schedule._orig_minute,
                    hour=schedule._orig_hour,
                    day_of_week=schedule._orig_day_of_week,
                    day_of_month=schedule._orig_day_of_month,
                    month_of_year=schedule._orig_month_of_year,
                )
            else:
                raise Exception(f"Unsupported schedule type: {type(schedule)}")

            PeriodicTask.objects.update_or_create(
                name=name,
                defaults={
                    "task": config["task"],
                    "schedule": schedule,
                    "enabled": config.get("enabled", True),
                    "kwargs": config.get("kwargs", "{}"),
                    "args": config.get("args", "[]"),
                    "expire_seconds": config.get("expire_seconds"),
                },
            )
