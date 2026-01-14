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
            choices=["simple", "detailed"],
            default="simple",
            help="Output format: 'simple' for summary table, 'detailed' for full breakdown",
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
        if format_type == "detailed":
            self._output_detailed(usage_data)
        else:
            self._output_simple(usage_data)

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

    def _output_simple(self, usage_data):
        """Output in simple table format."""
        self.stdout.write("\n" + "=" * 100)
        self.stdout.write(
            f"{'Model Name':<40} {'Provider':<20} {'Deprecated':<12} {'Total Uses':<10}  {'Replacement':<20}"
        )
        self.stdout.write("=" * 100)

        for data in usage_data:
            model = data["model"]
            deprecated_str = "Yes" if model.deprecated else "No"
            total_str = str(data["total"])
            team_suffix = f" (custom: {model.team.name})" if model.is_custom() else ""
            model_name = f"{model.name}{team_suffix}"
            replacement = data["suggested_replacement"] or "N/A"

            self.stdout.write(
                f"{model_name:<40} {model.type:<20} {deprecated_str:>10} {total_str:>12}  {replacement:<20}"
            )

        self.stdout.write("=" * 100 + "\n")

        # Summary
        total_models = len(usage_data)
        deprecated_models = sum(1 for d in usage_data if d["model"].deprecated)
        models_in_use = sum(1 for d in usage_data if d["total"] > 0)

        self.stdout.write("\nSummary:")
        self.stdout.write(f"  Total models: {total_models}")
        self.stdout.write(f"  Deprecated models: {deprecated_models}")
        self.stdout.write(f"  Models in use: {models_in_use}\n")

    def _output_detailed(
        self,
        usage_data,
    ):
        """Output in detailed format."""
        self.stdout.write("\n" + "=" * 100)
        self.stdout.write(self.style.SUCCESS("LLM Model Usage Report"))
        self.stdout.write("=" * 100 + "\n")

        for data in usage_data:
            model = data["model"]

            # Header
            self.stdout.write(f"\nModel: {self.style.SUCCESS(model.name)}")
            self.stdout.write(f"Provider: {model.type}")

            if model.is_custom():
                self.stdout.write(f"Type: Custom (Team: {model.team.name})")
            else:
                self.stdout.write("Type: Global")

            if model.deprecated:
                self.stdout.write(self.style.WARNING("Status: DEPRECATED"))
                if data["suggested_replacement"]:
                    self.stdout.write(f"Suggested replacement: {self.style.SUCCESS(data['suggested_replacement'])}")
            else:
                self.stdout.write("Status: Active")

            # Usage breakdown
            self.stdout.write("\nReferences:")
            self.stdout.write(f"  Experiments: {data['experiments']}")
            self.stdout.write(f"  Assistants: {data['assistants']}")
            self.stdout.write(f"  Analyses: {data['analyses']}")
            self.stdout.write(f"  Translation Analyses: {data['translation_analyses']}")
            self.stdout.write(f"  Pipeline Nodes: {data['pipeline_nodes']}")

            total_style = self.style.SUCCESS if data["total"] > 0 else self.style.WARNING
            self.stdout.write(total_style(f"  Total: {data['total']}"))

            # Show archived counts if not included
            if not data["include_archived"] and (data["archived_experiments"] > 0 or data["archived_assistants"] > 0):
                self.stdout.write("\nArchived (not counted above):")
                if data["archived_experiments"] > 0:
                    self.stdout.write(f"  Experiments: {data['archived_experiments']}")
                if data["archived_assistants"] > 0:
                    self.stdout.write(f"  Assistants: {data['archived_assistants']}")

            self.stdout.write("-" * 100)

        # Summary
        total_models = len(usage_data)
        deprecated_models = sum(1 for d in usage_data if d["model"].deprecated)
        deprecated_in_use = sum(1 for d in usage_data if d["model"].deprecated and d["total"] > 0)
        models_in_use = sum(1 for d in usage_data if d["total"] > 0)
        total_references = sum(d["total"] for d in usage_data)

        self.stdout.write("\n" + "=" * 100)
        self.stdout.write(self.style.SUCCESS("Summary"))
        self.stdout.write("=" * 100)
        self.stdout.write(f"Total models: {total_models}")
        self.stdout.write(f"Deprecated models: {deprecated_models}")
        if deprecated_in_use > 0:
            self.stdout.write(self.style.WARNING(f"Deprecated models still in use: {deprecated_in_use}"))
        self.stdout.write(f"Models in use: {models_in_use}")
        self.stdout.write(f"Total references: {total_references}")
        self.stdout.write("=" * 100 + "\n")
