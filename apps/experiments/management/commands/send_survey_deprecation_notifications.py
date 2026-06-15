from django.core.management.base import BaseCommand

from apps.experiments.models import Survey
from apps.ocs_notifications.notifications import survey_deprecation_notification
from apps.teams.models import Team


class Command(BaseCommand):
    help = "Send the one-off survey-deprecation notification to admins of teams with surveys."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List affected teams without notifying.")

    def handle(self, *args, **options):
        team_ids = Survey.objects.filter(is_version=False).values_list("team_id", flat=True).distinct()
        teams = Team.objects.filter(id__in=list(team_ids))
        self.stdout.write(f"{teams.count()} team(s) with surveys.")
        if options["dry_run"]:
            for team in teams:
                self.stdout.write(f"  would notify: {team.slug}")
            return
        for team in teams:
            survey_deprecation_notification(team)
            self.stdout.write(f"  notified: {team.slug}")
