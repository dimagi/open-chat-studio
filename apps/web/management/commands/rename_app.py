import logging

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.transaction import atomic

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Note: this does not rename DB tables or files, only the Django migrations and the Django content types."""

    help = "Renames a Django Application. Usage rename_app [old_app_name] [new_app_name]"

    def add_arguments(self, parser):
        parser.add_argument("old_app_name", nargs=1, type=str)
        parser.add_argument("new_app_name", nargs=1, type=str)

    @atomic
    def handle(self, old_app_name, new_app_name, *args, **options):
        with connection.cursor() as cursor:
            old_app_name = old_app_name[0]
            new_app_name = new_app_name[0]
            print(f"Renaming {old_app_name} to {new_app_name}, please wait...")
            cursor.execute(f"SELECT * FROM django_content_type where app_label='{new_app_name}'")

            has_already_been_ran = cursor.fetchone()

            if has_already_been_ran:
                logger.info("Rename has already been done, exiting without making any changes")
                print("Nothing to rename. Exiting.")
                return None

            cursor.execute(
                f"UPDATE django_content_type SET app_label='{new_app_name}' WHERE app_label='{old_app_name}'"
            )
            cursor.execute(f"UPDATE django_migrations SET app='{new_app_name}' WHERE app='{old_app_name}'")
