"""
Management command to delete a team and all its related data.

Usage:
    python manage.py delete_team test-team
    python manage.py delete_team test-team --force  # Skip confirmation
"""

from django.core.management.base import BaseCommand, CommandError

from apps.teams.models import Team
from apps.teams.utils import current_team
from apps.utils.deletion import delete_object_with_auditing_of_related_objects


class Command(BaseCommand):
    help = "Deletes a team and all its related data (experiments, files, participants, etc.)."

    def add_arguments(self, parser):
        parser.add_argument(
            "team_slug",
            type=str,
            help="The slug of the team to delete",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Skip confirmation prompt",
        )

    def handle(self, *args, **options):
        team_slug = options["team_slug"]
        force = options["force"]

        try:
            team = Team.objects.get(slug=team_slug)
        except Team.DoesNotExist:
            raise CommandError(f"Team with slug '{team_slug}' does not exist.") from None

        self.stdout.write(f"Team: {team.name} ({team.slug})")
        self.stdout.write(f"  Members: {team.members.count()}")

        if not force:
            confirm = input(f"\nAre you sure you want to delete team '{team.name}'? [y/N] ")
            if confirm.lower() != "y":
                self.stdout.write(self.style.WARNING("Aborted."))
                return

        self.stdout.write(f"\nDeleting team '{team.name}'...")

        with current_team(team):
            delete_object_with_auditing_of_related_objects(team)

        self.stdout.write(self.style.SUCCESS(f"Team '{team_slug}' deleted successfully."))
