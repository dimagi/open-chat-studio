import json

import pydantic
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.pipelines.flow import Flow, split_flow_data
from apps.pipelines.models import Pipeline
from apps.teams.models import Team


class Command(BaseCommand):
    help = "Create a new Pipeline and related models from a JSON file for a specified team."

    def add_arguments(self, parser):
        parser.add_argument("team", type=str, help="Slug of the team.")
        parser.add_argument("name", type=str, help="Name of the new pipeline.")
        parser.add_argument("file_path", type=str, help="Path to the JSON file.")

    @transaction.atomic
    def handle(self, *args, **options):
        team_slug = options["team"]
        name = options["name"]
        file_path = options["file_path"]

        try:
            with open(file_path) as file:
                data = json.load(file)
        except FileNotFoundError:
            raise CommandError(f"File {file_path} does not exist.") from None
        except json.JSONDecodeError as e:
            raise CommandError("Invalid JSON file.") from e

        try:
            team = Team.objects.get(slug=team_slug)
        except Team.DoesNotExist:
            raise CommandError(f"Team with slug {team_slug} does not exist.") from None

        try:
            flow = Flow(**data)
        except pydantic.ValidationError as e:
            raise CommandError(f"Invalid pipeline data: {e}") from e

        layout, node_data = split_flow_data(flow)
        new_pipeline = Pipeline.objects.create(
            team=team,
            data=layout.model_dump(),
            name=name,
        )
        new_pipeline.update_nodes_from_data(node_data)
        self.stdout.write(self.style.SUCCESS(f"Pipeline '{name}' created successfully: {new_pipeline.pk}"))
