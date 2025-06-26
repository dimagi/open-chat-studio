from django.core.management import call_command
from django.core.management.base import no_translations
from django.core.management.commands.migrate import Command as MigrateCommand


class Command(MigrateCommand):
    @no_translations
    def handle(self, *args, **options):
        super().handle(*args, **options)
        # migrate_finished.send(self)  # for some reason this doesn't work on prod
        from apps.teams.signals import create_groups_after_migrate

        create_groups_after_migrate()
        call_command("setup_periodic_tasks")
