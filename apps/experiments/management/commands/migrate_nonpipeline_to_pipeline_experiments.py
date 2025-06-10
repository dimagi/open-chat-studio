from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from apps.experiments.helper import convert_non_pipeline_experiment_to_pipeline
from apps.experiments.models import Experiment
from apps.teams.models import Flag


class Command(BaseCommand):
    help = "Convert assistant and LLM experiments to pipeline experiments"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be converted without making changes",
        )
        parser.add_argument(
            "--team-slug",
            type=str,
            help="Only convert experiments for a specific team (by slug)",
        )
        parser.add_argument(
            "--experiment-id",
            type=int,
            help="Convert only a specific experiment by ID",
        )
        parser.add_argument(
            "--chatbots-flag-only",
            action="store_true",
            help='Only convert experiments for teams that have the "flag_chatbots" feature flag enabled',
        )
        parser.add_argument(
            "--skip-confirmation",
            action="store_true",
            help="Skip confirmation prompt and proceed automatically",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        team_slug = options.get("team_slug")
        experiment_id = options.get("experiment_id")
        chatbots_flag_only = options["chatbots_flag_only"]
        skip_confirmation = options["skip_confirmation"]

        query = Q(pipeline__isnull=True) & (Q(assistant__isnull=False) | Q(llm_provider__isnull=False))

        if team_slug:
            query &= Q(team__slug=team_slug)

        if experiment_id:
            query &= Q(id=experiment_id)

        if chatbots_flag_only:
            chatbots_flag_team_ids = self._get_chatbots_flag_team_ids()
            if not chatbots_flag_team_ids:
                self.stdout.write(self.style.WARNING('No teams found with the "flag_chatbots" feature flag enabled.'))
                return
            query &= Q(team_id__in=chatbots_flag_team_ids)
            self.stdout.write(f"Filtering to teams with 'flag_chatbots' FF ({len(chatbots_flag_team_ids)} teams)")

        default_experiments = Experiment.objects.filter(query & Q(is_default_version=True))
        default_working_version_ids = default_experiments.exclude(working_version__isnull=True).values_list(
            "working_version_id", flat=True
        )

        working_experiments = Experiment.objects.filter(query & Q(working_version__isnull=True)).exclude(
            id__in=default_working_version_ids
        )
        combined_ids = list(default_experiments.union(working_experiments).values_list("id", flat=True))

        experiments_to_convert = Experiment.objects.filter(id__in=combined_ids).select_related(
            "team", "assistant", "llm_provider", "llm_provider_model"
        )

        if not experiments_to_convert.exists():
            self.stdout.write(self.style.WARNING("No matching experiments found."))
            return

        self.stdout.write(f"Found {experiments_to_convert.count()} experiments to migrate:")

        for experiment in experiments_to_convert:
            experiment_type = self._get_experiment_type(experiment)
            team_info = f"{experiment.team.name} ({experiment.team.slug})"
            self.stdout.write(f"{experiment.name} (ID: {experiment.id}) - Type: {experiment_type} - Team: {team_info}")

        if dry_run:
            self.stdout.write(self.style.WARNING("\nDry run - no changes will be made."))
            return

        if not skip_confirmation:
            confirm = input("\nContinue? (y/N): ")
            if confirm.lower() != "y":
                self.stdout.write("Cancelled.")
                return

        converted_count = 0
        failed_count = 0

        for experiment in experiments_to_convert:
            try:
                with transaction.atomic():
                    convert_non_pipeline_experiment_to_pipeline(experiment)
                    converted_count += 1
                    self.stdout.write(self.style.SUCCESS(f"Success: {experiment.name}"))
            except Exception as e:
                failed_count += 1
                self.stdout.write(self.style.ERROR(f"FAILED {experiment.name}: {str(e)}"))

        self.stdout.write(
            self.style.SUCCESS(f"\nMigration is complete!: {converted_count} succeeded, {failed_count} failed")
        )

    def _get_experiment_type(self, experiment):
        if experiment.assistant:
            return "Assistant"
        elif experiment.llm_provider:
            return "LLM"
        else:
            return "Unknown"
        experiment.save()

    def _get_chatbots_flag_team_ids(self):
        chatbots_flag = Flag.objects.get(name="flag_chatbots")
        return list(chatbots_flag.teams.values_list("id", flat=True))
