import traceback

from django.core.management import BaseCommand, CommandError

from apps.experiments.models import Experiment
from apps.teams.models import Flag
from apps.teams.utils import current_team


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("team", help="The team slug to enable versioning for.")
        parser.add_argument("--force", action="store_true", help="Force enable versioning.")
        parser.add_argument(
            "--continue-on-error", action="store_true", help="Continue creating versions even if an error occurs."
        )

    def handle(self, team, **options):
        from apps.teams.models import Team

        try:
            team = Team.objects.get(slug=team)
        except Team.DoesNotExist:
            raise CommandError(f"Team {team} does not exist.")

        flag = Flag.get("experiment_versions")
        flag_is_active = flag.is_active_for_team(team)
        if flag_is_active and not options["force"]:
            raise CommandError(f"Versioning is already enabled for team {team.slug}.")

        with current_team(team):
            for experiment in Experiment.objects.filter(team=team, working_version_id=None).order_by("-id"):
                if not Experiment.objects.filter(working_version=experiment).exists():
                    self.stdout.write(f"Creating version for experiment {experiment.name} ({experiment.id}).")
                    try:
                        experiment.create_new_version()
                    except Exception as e:
                        traceback.print_exception(type(e), e, e.__traceback__)
                        if options["continue_on_error"]:
                            self.stderr.write(
                                f"Failed to create version for experiment {experiment.name} ({experiment.id}): {e}"
                            )
                        else:
                            raise CommandError(
                                f"Failed to create version for experiment {experiment.name} ({experiment.id}): {e}"
                            )

        if not flag_is_active:
            flag.teams.add(team)
            flag.flush()

        self.stdout.write(f"Versioning enabled for team {team.slug}.")
