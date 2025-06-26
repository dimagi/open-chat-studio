from celery import current_app
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django_celery_beat.models import PeriodicTask
from django_celery_beat.schedulers import ModelEntry


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
        PeriodicTask.objects.exclude(name__in=tasks).exclude(name__startswith="celery.").delete()

        app = current_app._get_current_object()
        for name, config in scheduled_tasks.items():
            self.stdout.write(f"Updating periodic task {name}")
            ModelEntry.from_entry(name, app=app, **config)
