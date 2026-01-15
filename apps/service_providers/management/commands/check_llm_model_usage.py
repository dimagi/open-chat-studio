from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.analysis.models import TranscriptAnalysis
from apps.assistants.models import OpenAiAssistant
from apps.experiments.models import Experiment
from apps.pipelines.models import Node
from apps.service_providers.llm_service.default_models import DEFAULT_LLM_PROVIDER_MODELS
from apps.service_providers.models import LlmProviderModel


class Command(BaseCommand):
    help = "Check LLM model usage across the system"

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            type=str,
            choices=["table", "csv"],
            default="table",
            help="Output format: 'table' for console table, 'csv' for CSV output",
        )
        parser.add_argument(
            "--deprecated-only",
            action="store_true",
            help="Show only deprecated models",
        )
        parser.add_argument(
            "--in-use-only",
            action="store_true",
            help="Show only models with at least one reference",
        )
        parser.add_argument(
            "--include-archived",
            action="store_true",
            help="Include archived versions in the count",
        )

    def handle(self, *args, **options):
        format_type = options["format"]
        deprecated_only = options["deprecated_only"]
        in_use_only = options["in_use_only"]
        include_archived = options["include_archived"]

        # Get all models
        models_queryset = LlmProviderModel.objects.all()

        if deprecated_only:
            models_queryset = models_queryset.filter(deprecated=True)

        models = models_queryset.select_related("team").order_by("type", "name")

        if not models.exists():
            self.stdout.write(self.style.WARNING("No LLM models found matching the criteria."))
            return

        # Collect usage data
        usage_data = []
        for model in models:
            data = self._get_model_usage(model, include_archived)

            # Filter by in_use_only if requested
            if in_use_only and data["total"] == 0:
                continue

            usage_data.append(data)

        # Sort models by total usage
        usage_data = sorted(usage_data, key=lambda d: d["total"], reverse=True)

        # Output based on format
        if format_type == "csv":
            self._output_csv(usage_data)
        else:
            self._output_table(usage_data)

    def _get_model_usage(self, model, include_archived=False):
        """Get usage statistics for a single model."""
        # Direct FK references - use appropriate manager
        if include_archived:
            # Use get_all() for Experiment (VersionsObjectManagerMixin)
            experiments_count = Experiment.objects.get_all().filter(llm_provider_model=model).count()
            assistants_count = OpenAiAssistant.all_objects.filter(llm_provider_model=model).count()
        else:
            experiments_count = Experiment.objects.filter(llm_provider_model=model).count()
            assistants_count = OpenAiAssistant.objects.filter(llm_provider_model=model).count()

        analyses_count = TranscriptAnalysis.objects.filter(llm_provider_model=model).count()
        translation_analyses_count = TranscriptAnalysis.objects.filter(translation_llm_provider_model=model).count()

        # Pipeline nodes (JSON field references)
        pipeline_nodes_count = Node.objects.filter(
            Q(params__llm_provider_model_id=model.id) | Q(params__llm_provider_model_id=str(model.id))
        ).count()

        total_count = (
            experiments_count + assistants_count + analyses_count + translation_analyses_count + pipeline_nodes_count
        )

        # Count archived separately for detailed view
        archived_experiments = 0
        archived_assistants = 0
        if not include_archived:
            archived_experiments = (
                Experiment.objects.get_all().filter(llm_provider_model=model, is_archived=True).count()
            )
            archived_assistants = OpenAiAssistant.all_objects.filter(llm_provider_model=model, is_archived=True).count()

        # Get suggested replacement for deprecated models
        suggested_replacement = None
        if model.deprecated:
            suggested_replacement = self._get_suggested_replacement(model)

        return {
            "model": model,
            "experiments": experiments_count,
            "assistants": assistants_count,
            "analyses": analyses_count,
            "translation_analyses": translation_analyses_count,
            "pipeline_nodes": pipeline_nodes_count,
            "total": total_count,
            "archived_experiments": archived_experiments,
            "archived_assistants": archived_assistants,
            "suggested_replacement": suggested_replacement,
            "include_archived": include_archived,
        }

    def _get_suggested_replacement(self, model):
        """Get suggested replacement model for a deprecated model."""
        # Try to find the default model for the same provider
        provider_models = DEFAULT_LLM_PROVIDER_MODELS.get(model.type, [])
        for provider_model in provider_models:
            if provider_model.is_default:
                return provider_model.name
        return None

    def _output_table(self, usage_data):
        """Output in detailed table format."""
        self.stdout.write("\n" + "=" * 180)
        self.stdout.write(self.style.SUCCESS("LLM Model Usage Report"))
        self.stdout.write("=" * 180)

        # Table header
        header = (
            f"{'Model':<35} {'Provider':<15} {'Type':<12} {'Depr':<5} "
            f"{'Expt':<5} {'Asst':<5} {'Anly':<5} {'Tran':<5} {'Pipe':<5} {'Total':<6} "
            f"{'Arch-E':<6} {'Arch-A':<6} {'Replacement':<20}"
        )
        self.stdout.write(header)
        self.stdout.write("-" * 180)

        # Table rows
        for data in usage_data:
            model = data["model"]
            model_type = "Custom" if model.is_custom() else "Global"
            deprecated = "Yes" if model.deprecated else "No"

            # Truncate model name if too long
            model_name = model.name
            if len(model_name) > 34:
                model_name = model_name[:31] + "..."

            replacement = data["suggested_replacement"] or "-"
            if len(replacement) > 19:
                replacement = replacement[:16] + "..."

            row = (
                f"{model_name:<35} {model.type:<15} {model_type:<12} {deprecated:<5} "
                f"{data['experiments']:<5} {data['assistants']:<5} {data['analyses']:<5} "
                f"{data['translation_analyses']:<5} {data['pipeline_nodes']:<5} {data['total']:<6} "
                f"{data['archived_experiments']:<6} {data['archived_assistants']:<6} {replacement:<20}"
            )

            # Color code deprecated models in use
            if model.deprecated and data["total"] > 0:
                self.stdout.write(self.style.WARNING(row))
            else:
                self.stdout.write(row)

        self.stdout.write("=" * 180)

        # Summary
        total_models = len(usage_data)
        deprecated_models = sum(1 for d in usage_data if d["model"].deprecated)
        deprecated_in_use = sum(1 for d in usage_data if d["model"].deprecated and d["total"] > 0)
        models_in_use = sum(1 for d in usage_data if d["total"] > 0)
        total_references = sum(d["total"] for d in usage_data)

        self.stdout.write("\n" + self.style.SUCCESS("Summary:"))
        self.stdout.write(f"  Total models: {total_models}")
        self.stdout.write(f"  Deprecated models: {deprecated_models}")
        if deprecated_in_use > 0:
            self.stdout.write(self.style.WARNING(f"  Deprecated models still in use: {deprecated_in_use}"))
        self.stdout.write(f"  Models in use: {models_in_use}")
        self.stdout.write(f"  Total references: {total_references}")

        if usage_data and not usage_data[0]["include_archived"]:
            self.stdout.write(
                "\nNote: Arch-E = Archived Experiments, Arch-A = Archived Assistants (not counted in Total)"
            )
        self.stdout.write("")

    def _output_csv(self, usage_data):
        """Output in CSV format."""
        import csv
        import sys

        writer = csv.writer(sys.stdout)

        # CSV header
        writer.writerow([
            "Model Name",
            "Provider",
            "Type",
            "Deprecated",
            "Experiments",
            "Assistants",
            "Analyses",
            "Translation Analyses",
            "Pipeline Nodes",
            "Total",
            "Archived Experiments",
            "Archived Assistants",
            "Team",
            "Suggested Replacement",
        ])

        # CSV rows
        for data in usage_data:
            model = data["model"]
            model_type = "Custom" if model.is_custom() else "Global"
            team_name = model.team.name if model.is_custom() else ""

            writer.writerow([
                model.name,
                model.type,
                model_type,
                "Yes" if model.deprecated else "No",
                data["experiments"],
                data["assistants"],
                data["analyses"],
                data["translation_analyses"],
                data["pipeline_nodes"],
                data["total"],
                data["archived_experiments"],
                data["archived_assistants"],
                team_name,
                data["suggested_replacement"] or "",
            ])
