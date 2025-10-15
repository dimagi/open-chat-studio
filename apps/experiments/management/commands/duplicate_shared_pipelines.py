from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from apps.experiments.models import Experiment
from apps.pipelines.models import Node, Pipeline


class Command(BaseCommand):
    help = "Find pipelines that are shared by multiple experiments and create individual copies\
    for each experiment (except one)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        shared_pipelines = (
            Pipeline.objects.get_all()
            .annotate(experiment_count=Count("experiment"))
            .filter(experiment_count__gt=1, working_version=None)
        )

        if not shared_pipelines.exists():
            self.stdout.write(self.style.SUCCESS("No shared pipelines found."))
            return

        self.stdout.write(f"Found {shared_pipelines.count()} shared pipelines to duplicate")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes will be made"))
        else:
            confirm = input("Do you want to proceed? (y/N): ")
            if confirm.lower() != "y":
                self.stdout.write("Cancelled.")
                return

        total_created = 0

        for shared_pipeline in shared_pipelines:
            self.stdout.write(f"Processing pipeline: {shared_pipeline.name} (ID: {shared_pipeline.id})")

            experiments = list(Experiment.objects.filter(pipeline=shared_pipeline))
            self.stdout.write(f"  Shared by {len(experiments)} experiments")

            if dry_run:
                self.stdout.write(f"  Would create {len(experiments) - 1} pipeline copies")
                total_created += len(experiments) - 1
                continue

            for _, experiment in enumerate(experiments[1:], 1):
                try:
                    with transaction.atomic():
                        new_pipeline = Pipeline.objects.create(
                            team=shared_pipeline.team,
                            name=f"{shared_pipeline.name} (Copy for {experiment.name})",
                            data=shared_pipeline.data,
                            working_version=None,
                            version_number=1,
                            is_archived=shared_pipeline.is_archived,
                        )
                        original_nodes = Node.objects.filter(pipeline=shared_pipeline)
                        for node in original_nodes:
                            Node.objects.create(
                                flow_id=node.flow_id,
                                type=node.type,
                                label=node.label,
                                params=node.params,
                                working_version=None,
                                is_archived=node.is_archived,
                                pipeline=new_pipeline,
                            )
                        experiment.pipeline = new_pipeline
                        experiment.save(update_fields=["pipeline"])

                        self.stdout.write(f"  Created pipeline copy {new_pipeline.id} for experiment {experiment.name}")
                        total_created += 1

                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"  Error creating pipeline copy for experiment {experiment.name}: {str(e)}")
                    )
                    raise

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"DRY RUN: Would create {total_created} pipeline copies"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Successfully created {total_created} pipeline copies"))
