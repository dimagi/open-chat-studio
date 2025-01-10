import traceback

from django.core.management import BaseCommand

from apps.experiments.models import Experiment
from apps.teams.models import Flag
from apps.teams.utils import current_team


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("teams", help="The team slug to enable versioning for.")
        parser.add_argument("--force", action="store_true", help="Force enable versioning.")

    def handle(self, teams, **options):
        team_slugs = teams.split(",")
        for slug in team_slugs:
            if not slug.strip():
                continue
            self._enable_for_team(slug.strip(), **options)

    def _enable_for_team(self, team_slug, **options):
        from apps.teams.models import Team

        try:
            team = Team.objects.get(slug=team_slug)
        except Team.DoesNotExist:
            self.stderr.write(f"Team {team} does not exist.")
            return

        flag = Flag.get("experiment_versions")
        flag_is_active = flag.is_active_for_team(team)
        if flag_is_active:
            self.stdout.write(f"Versioning is already enabled for team {team.slug}.")
            if not options["force"]:
                return
            else:
                self.stdout.write("Forcing versioning.")

        with current_team(team):
            for experiment in Experiment.objects.filter(team=team, working_version_id=None).order_by("-id"):
                if not Experiment.objects.filter(working_version=experiment).exists():
                    self.stdout.write(f"Creating version for experiment {experiment.name} ({experiment.id}).")
                    try:
                        experiment.create_new_version()
                    except Exception as e:
                        traceback.print_exception(type(e), e, e.__traceback__)
                        self.stderr.write(
                            f"Failed to create version for experiment {experiment.name} ({experiment.id}): {e}"
                        )

        if not flag_is_active:
            flag.teams.add(team)
            flag.flush()

        self.stdout.write(f"Versioning enabled for team {team.slug}.")
