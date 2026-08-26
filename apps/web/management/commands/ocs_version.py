from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    # Not named `version`: Django's ManagementUtility intercepts that subcommand
    # and prints its own version before command lookup ever happens.
    help = "Print the OCS release this deployment was built from."

    def handle(self, *args, **options):
        self.stdout.write(settings.OCS_BUILD_VERSION)
