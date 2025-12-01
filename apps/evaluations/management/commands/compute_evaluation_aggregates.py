from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.evaluations.aggregation import compute_aggregates_for_run
from apps.evaluations.models import EvaluationRun, EvaluationRunStatus, EvaluationRunType


class Command(IdempotentCommand):
    help = "Compute aggregates for completed evaluation runs that are missing them"
    migration_name = "compute_evaluation_aggregates_2024_12_01"
    atomic = False

    def perform_migration(self, dry_run=False):
        queryset = EvaluationRun.objects.filter(
            status=EvaluationRunStatus.COMPLETED,
            type=EvaluationRunType.FULL,
        ).exclude(aggregates__isnull=False)

        runs = list(queryset.order_by("created_at"))
        total = len(runs)

        if total == 0:
            self.stdout.write("No runs to process")
            return 0

        if dry_run:
            self.stdout.write(f"Would process {total} runs")
            return total

        self.stdout.write(f"Processing {total} runs...")

        processed = 0
        aggregates_created = 0

        for run in runs:
            aggregates = compute_aggregates_for_run(run)
            aggregates_created += len(aggregates)
            processed += 1

            if processed % 100 == 0:
                self.stdout.write(f"  Processed {processed}/{total} runs...")

        self.stdout.write(f"Processed {processed} runs, created {aggregates_created} aggregates")
        return processed
