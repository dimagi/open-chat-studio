from django.core.management.base import no_translations
from django.core.management.commands.migrate import Command as MigrateCommand

from apps.web.signals import migrate_finished


class Command(MigrateCommand):
    @no_translations
    def handle(self, *args, **options):
        super().handle(*args, **options)
        migrate_finished.send(self)
