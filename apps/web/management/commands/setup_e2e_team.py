"""
Management command to create an empty team and owner user for the core E2E test.

Deletes and recreates the team on every run so the E2E test starts from a team with
no LLM providers, chatbots or sessions. Also deletes the teams the user created, which
are left over from earlier E2E runs. No other team is touched. Refuses to run unless
DEBUG or CI is on, so it cannot delete data on a real deployment.

Usage:
    python manage.py setup_e2e_team
    python manage.py setup_e2e_team --email e2e@example.com --password e2epassword --team-slug e2e-core
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.teams import backends
from apps.teams.models import Team
from apps.teams.utils import current_team
from apps.utils.deletion import delete_object_with_auditing_of_related_objects


class Command(BaseCommand):
    help = "Creates an empty team with a single owner user for the core E2E test."

    def add_arguments(self, parser):
        parser.add_argument("--email", type=str, default="e2e@example.com", help="Email for the user")
        parser.add_argument("--password", type=str, default="e2epassword", help="Password for the user")
        parser.add_argument("--team-slug", type=str, default="e2e-core", help="Slug for the team")
        parser.add_argument("--team-name", type=str, default="E2E Core", help="Name for the team")

    def handle(self, *args, **options):
        if not (settings.DEBUG or settings.IS_CI):
            raise CommandError("setup_e2e_team deletes teams, so it only runs with DEBUG or CI on.")
        email = options["email"]
        team_slug = options["team_slug"]
        self._check_user_is_disposable(email=email, team_slug=team_slug)

        backends.create_default_groups()
        for team in Team.objects.filter(created_by__email=email):
            self._delete_team(team)
        if team := Team.objects.filter(slug=team_slug).first():
            self._delete_team(team)
        team = Team.objects.create(slug=team_slug, name=options["team_name"])
        with current_team(team):
            user = self._create_user(email=email, password=options["password"])
            backends.make_user_team_owner(team=team, user=user)

        self.stdout.write(self.style.SUCCESS(f"Team '{team_slug}' ready with owner {email}"))

    def _check_user_is_disposable(self, email: str, team_slug: str):
        """Refuse to reset the password of a user who is a member of a team this command does not own."""
        other_teams = (
            Team.objects.filter(membership__user__username=email)
            .exclude(created_by__email=email)
            .exclude(slug=team_slug)
        )
        if other_teams.exists():
            raise CommandError(f"{email} belongs to other teams, so setup_e2e_team will not reset its password.")

    def _create_user(self, email: str, password: str):
        user, _ = get_user_model().objects.get_or_create(username=email, defaults={"email": email})
        user.set_password(password)
        user.save()
        return user

    def _delete_team(self, team: Team):
        with current_team(team):
            delete_object_with_auditing_of_related_objects(team)
        self.stdout.write(f"Deleted team '{team.slug}'")
