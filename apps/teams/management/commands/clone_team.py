"""
Management command to batch clone teams with all related data.

Usage:
    python manage.py clone_team --source-team=demo_team --count=10 \
        --name-template="client_team_{n}" \
        --email-template="demo{n}@example.org" \
        --password-template="password{n}" \
        --start-index=1 \
        --dry-run
"""

from dataclasses import dataclass, field

from allauth.account.models import EmailAddress
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.evaluations.models import EvaluationConfig, EvaluationDataset, EvaluationMessage, Evaluator
from apps.experiments.models import ConsentForm, Experiment, SourceMaterial, Survey
from apps.pipelines.models import Node, Pipeline
from apps.service_providers.models import LlmProvider, LlmProviderModel, TraceProvider, VoiceProvider
from apps.teams import backends
from apps.teams.models import Flag, Team
from apps.teams.utils import current_team
from apps.users.models import CustomUser
from apps.utils.deletion import delete_object_with_auditing_of_related_objects


@dataclass
class CloneContext:
    """Tracks old_id -> new_instance mappings during cloning."""

    source_team: Team
    target_team: Team
    user: CustomUser = None

    # Phase 2: Providers
    llm_providers: dict[int, LlmProvider] = field(default_factory=dict)
    llm_provider_models: dict[int, LlmProviderModel] = field(default_factory=dict)
    voice_providers: dict[int, VoiceProvider] = field(default_factory=dict)
    trace_providers: dict[int, TraceProvider] = field(default_factory=dict)

    # Phase 3: Content
    source_materials: dict[int, SourceMaterial] = field(default_factory=dict)
    consent_forms: dict[int, ConsentForm] = field(default_factory=dict)
    surveys: dict[int, Survey] = field(default_factory=dict)

    # Phase 4: Complex
    pipelines: dict[int, Pipeline] = field(default_factory=dict)
    experiments: dict[int, Experiment] = field(default_factory=dict)

    # Phase 5: Evaluations
    evaluators: dict[int, Evaluator] = field(default_factory=dict)
    datasets: dict[int, EvaluationDataset] = field(default_factory=dict)


class Command(BaseCommand):
    help = "Clone a team with all related data to create multiple demo teams."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-team",
            type=str,
            required=True,
            help="Source team slug to clone from",
        )
        parser.add_argument(
            "--count",
            type=int,
            required=True,
            help="Number of teams to create",
        )
        parser.add_argument(
            "--name-template",
            type=str,
            required=True,
            help="Team name template with {n} placeholder (e.g., 'client_team_{n}')",
        )
        parser.add_argument(
            "--email-template",
            type=str,
            required=True,
            help="User email template with {n} placeholder (e.g., 'demo{n}@example.org')",
        )
        parser.add_argument(
            "--password-template",
            type=str,
            required=True,
            help="User password template with {n} placeholder (e.g., 'password{n}')",
        )
        parser.add_argument(
            "--start-index",
            type=int,
            default=1,
            help="Starting index for {n} placeholder (default: 1)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be created without making changes",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Skip confirmation prompt",
        )

    def handle(self, *args, **options):
        source_slug = options["source_team"]
        count = options["count"]
        name_template = options["name_template"]
        email_template = options["email_template"]
        password_template = options["password_template"]
        start_index = options["start_index"]
        dry_run = options["dry_run"]
        force = options["force"]

        # Validate source team exists
        try:
            source_team = Team.objects.get(slug=source_slug)
        except Team.DoesNotExist:
            raise CommandError(f"Source team '{source_slug}' does not exist.") from None

        # Preview source team data and targets
        self._preview_source(source_team)
        self._preview_targets(name_template, email_template, password_template, start_index, count)

        if dry_run:
            self.stdout.write(self.style.WARNING("\n=== DRY RUN MODE - no changes made ==="))
            return

        # Confirmation prompt
        if not force:
            confirm = input(f"\nProceed with cloning {count} team(s)? [y/N] ")
            if confirm.lower() != "y":
                self.stdout.write(self.style.WARNING("Aborted."))
                return

        # Clone teams
        created_teams = []
        failed = []

        for i in range(start_index, start_index + count):
            team_name = name_template.format(n=i)
            email = email_template.format(n=i)
            password = password_template.format(n=i)
            slug = team_name.lower().replace(" ", "_").replace("-", "_")

            self.stdout.write(f"\n--- Cloning team {i}: {team_name} ---")

            target_team = None
            try:
                with transaction.atomic():
                    ctx = self._clone_team(source_team, team_name, slug, email, password)
                    target_team = ctx.target_team
                    self.stdout.write(self.style.SUCCESS(f"  Created: {target_team.slug}"))
                created_teams.append(target_team)
            except Exception as e:
                failed.append((team_name, str(e)))
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
                # Check if team was partially created (transaction should rollback, but be safe)
                partial_team = Team.objects.filter(slug=slug).first()
                if partial_team:
                    created_teams.append(partial_team)

        # Summary
        self.stdout.write("\n=== Summary ===")
        self.stdout.write(f"Created: {len(created_teams)}")
        self.stdout.write(f"Failed: {len(failed)}")

        if failed:
            self.stdout.write(self.style.ERROR("\nFailed teams:"))
            for name, error in failed:
                self.stdout.write(f"  {name}: {error}")

            # Prompt to delete created teams on failure
            if created_teams:
                self.stdout.write("")
                confirm = input(f"Delete the {len(created_teams)} successfully created team(s)? [y/N] ")
                if confirm.lower() == "y":
                    for team in created_teams:
                        self.stdout.write(f"Deleting {team.slug}...")
                        with current_team(team):
                            delete_object_with_auditing_of_related_objects(team)
                    self.stdout.write(self.style.SUCCESS("Deleted all created teams."))

    def _preview_source(self, team: Team):
        """Display source team data counts."""
        self.stdout.write(f"\nSource team: {team.name} ({team.slug})")
        self.stdout.write("  Data to clone:")
        self.stdout.write(f"    LLM Providers: {LlmProvider.objects.filter(team=team).count()}")
        self.stdout.write(f"    LLM Provider Models: {LlmProviderModel.objects.filter(team=team).count()}")
        self.stdout.write(f"    Voice Providers: {VoiceProvider.objects.filter(team=team).count()}")
        self.stdout.write(f"    Trace Providers: {TraceProvider.objects.filter(team=team).count()}")
        sm_count = SourceMaterial.objects.working_versions_queryset().filter(team=team).count()
        self.stdout.write(f"    Source Materials: {sm_count}")
        cf_count = ConsentForm.objects.working_versions_queryset().filter(team=team).count()
        self.stdout.write(f"    Consent Forms: {cf_count}")
        survey_count = Survey.objects.working_versions_queryset().filter(team=team).count()
        self.stdout.write(f"    Surveys: {survey_count}")
        pipeline_count = Pipeline.objects.working_versions_queryset().filter(team=team).count()
        self.stdout.write(f"    Pipelines: {pipeline_count}")
        exp_count = Experiment.objects.working_versions_queryset().filter(team=team).count()
        self.stdout.write(f"    Experiments: {exp_count}")
        self.stdout.write(f"    Evaluators: {Evaluator.objects.filter(team=team).count()}")
        self.stdout.write(f"    Evaluation Datasets: {EvaluationDataset.objects.filter(team=team).count()}")
        self.stdout.write(f"    Evaluation Configs: {EvaluationConfig.objects.filter(team=team).count()}")
        self.stdout.write(f"    Feature Flags: {Flag.objects.filter(teams=team).count()}")

    def _preview_targets(self, name_template, email_template, password_template, start_index, count):
        """Preview what teams would be created (first 5 only)."""
        self.stdout.write(f"\nWould create {count} team(s):")
        preview_count = min(count, 5)
        for i in range(start_index, start_index + preview_count):
            team_name = name_template.format(n=i)
            email = email_template.format(n=i)
            slug = team_name.lower().replace(" ", "_").replace("-", "_")
            self.stdout.write(f"  Team: {team_name} (slug: {slug})")
            self.stdout.write(f"    Owner: {email}")
        if count > 5:
            self.stdout.write(f"  ... and {count - 5} more")

    def _clone_team(
        self,
        source_team: Team,
        name: str,
        slug: str,
        email: str,
        password: str,
    ) -> CloneContext:
        """Clone a team with all related data."""
        # Phase 1: Create team and user
        target_team = Team.objects.create(name=name, slug=slug)

        user = CustomUser.objects.create_user(username=email, email=email, password=password)
        EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
        backends.make_user_team_owner(target_team, user)

        ctx = CloneContext(source_team=source_team, target_team=target_team, user=user)

        # Set team context for audit logging
        with current_team(target_team):
            # Add target team to same feature flags as source team
            self._clone_feature_flags(ctx)

            # Phase 2: Clone providers
            self._clone_providers(ctx)

            # Phase 3: Clone content (versioned models)
            self._clone_content(ctx)

            # Phase 4: Clone experiments (which also copies their pipelines)
            self._clone_experiments(ctx)

            # Phase 5: Clone evaluations
            self._clone_evaluations(ctx)

        return ctx

    def _clone_feature_flags(self, ctx: CloneContext):
        """Add target team to same feature flags as source team."""
        for flag in Flag.objects.filter(teams=ctx.source_team):
            flag.teams.add(ctx.target_team)
            flag.save()

    def _clone_providers(self, ctx: CloneContext):
        """Clone LLM, Voice, and Trace providers."""
        # LLM Providers
        for provider in LlmProvider.objects.filter(team=ctx.source_team):
            new_provider = LlmProvider.objects.create(
                team=ctx.target_team,
                type=provider.type,
                name=provider.name,
                config=provider.config,  # Encrypted config copied as-is
            )
            ctx.llm_providers[provider.id] = new_provider

        # LLM Provider Models (team-scoped only)
        for model in LlmProviderModel.objects.filter(team=ctx.source_team):
            new_model = LlmProviderModel.objects.create(
                team=ctx.target_team,
                type=model.type,
                name=model.name,
                max_token_limit=model.max_token_limit,
            )
            ctx.llm_provider_models[model.id] = new_model

        # Voice Providers
        for provider in VoiceProvider.objects.filter(team=ctx.source_team):
            new_provider = VoiceProvider.objects.create(
                team=ctx.target_team,
                type=provider.type,
                name=provider.name,
                config=provider.config,
            )
            ctx.voice_providers[provider.id] = new_provider

        # Trace Providers
        for provider in TraceProvider.objects.filter(team=ctx.source_team):
            new_provider = TraceProvider.objects.create(
                team=ctx.target_team,
                type=provider.type,
                name=provider.name,
                config=provider.config,
            )
            ctx.trace_providers[provider.id] = new_provider

    def _clone_content(self, ctx: CloneContext):
        """Clone versioned content models (working versions only)."""
        # Source Materials
        for sm in SourceMaterial.objects.working_versions_queryset().filter(team=ctx.source_team):
            new_sm = SourceMaterial.objects.create(
                team=ctx.target_team,
                owner=ctx.user,
                topic=sm.topic,
                description=sm.description,
                material=sm.material,
            )
            ctx.source_materials[sm.id] = new_sm

        # Consent Forms - default consent form is auto-created by signal on Team creation
        # So we skip cloning the default consent form and map it to the auto-created one
        default_consent = ConsentForm.objects.filter(team=ctx.target_team, is_default=True).first()
        for cf in ConsentForm.objects.working_versions_queryset().filter(team=ctx.source_team):
            if cf.is_default:
                # Map source default to target's auto-created default
                if default_consent:
                    ctx.consent_forms[cf.id] = default_consent
            else:
                new_cf = ConsentForm.objects.create(
                    team=ctx.target_team,
                    name=cf.name,
                    consent_text=cf.consent_text,
                    capture_identifier=cf.capture_identifier,
                    identifier_label=cf.identifier_label,
                    identifier_type=cf.identifier_type,
                    confirmation_text=cf.confirmation_text,
                    is_default=False,
                )
                ctx.consent_forms[cf.id] = new_cf

        # Surveys
        for survey in Survey.objects.working_versions_queryset().filter(team=ctx.source_team):
            new_survey = Survey.objects.create(
                team=ctx.target_team,
                name=survey.name,
                url=survey.url,
                confirmation_text=survey.confirmation_text,
            )
            ctx.surveys[survey.id] = new_survey

    def _remap_node_params(self, ctx: CloneContext, node: Node):
        """Remap FK IDs in node params to new team's objects."""
        params = node.params
        changed = False

        # Fail if unmapped params have values (these reference objects we don't clone)
        unmapped_params = ["assistant_id", "collection_id", "collection_index_ids", "synthetic_voice_id"]
        for param in unmapped_params:
            if param in params and params[param]:
                raise CommandError(
                    f"Pipeline node '{node.label}' has {param}={params[param]} which cannot be cloned. "
                    f"Remove or clear this reference in the source pipeline before cloning."
                )

        # Remap llm_provider_id
        if "llm_provider_id" in params and params["llm_provider_id"]:
            old_id = int(params["llm_provider_id"])
            if old_id in ctx.llm_providers:
                params["llm_provider_id"] = ctx.llm_providers[old_id].id
                changed = True
            elif not LlmProvider.objects.filter(id=old_id, team__isnull=True).exists():
                # Not a global provider and not in our mapping - error
                raise CommandError(
                    f"Pipeline node '{node.label}' references llm_provider_id={old_id} "
                    f"which was not found in source team."
                )
            # else: global provider, leave as-is

        # Remap llm_provider_model_id
        if "llm_provider_model_id" in params and params["llm_provider_model_id"]:
            old_id = int(params["llm_provider_model_id"])
            if old_id in ctx.llm_provider_models:
                params["llm_provider_model_id"] = ctx.llm_provider_models[old_id].id
                changed = True
            elif not LlmProviderModel.objects.filter(id=old_id, team__isnull=True).exists():
                # Not a global model and not in our mapping - error
                raise CommandError(
                    f"Pipeline node '{node.label}' references llm_provider_model_id={old_id} "
                    f"which was not found in source team."
                )
            # else: global model, leave as-is

        # Remap source_material_id
        if "source_material_id" in params and params["source_material_id"]:
            old_id = int(params["source_material_id"])
            if old_id not in ctx.source_materials:
                raise CommandError(
                    f"Pipeline node '{node.label}' references source_material_id={old_id} "
                    f"which was not found in source team."
                )
            params["source_material_id"] = ctx.source_materials[old_id].id
            changed = True

        if changed:
            node.params = params
            node.save(update_fields=["params"])

    def _clone_experiments(self, ctx: CloneContext):
        """Clone experiments and remap team + FKs."""
        for experiment in Experiment.objects.working_versions_queryset().filter(team=ctx.source_team):
            # Fail if experiment has events (triggers) - these are not cloned
            if experiment.static_triggers.exists():
                raise CommandError(
                    f"Experiment '{experiment.name}' has static triggers which cannot be cloned. "
                    f"Remove events before cloning."
                )
            if experiment.timeout_triggers.exists():
                raise CommandError(
                    f"Experiment '{experiment.name}' has timeout triggers which cannot be cloned. "
                    f"Remove events before cloning."
                )

            # Use create_new_version(is_copy=True) for independent copy
            # This also copies the pipeline if present
            new_exp = experiment.create_new_version(is_copy=True, name=experiment.name)
            new_exp.team = ctx.target_team
            new_exp.owner = ctx.user

            # Remap FK relationships - error if mapping not found
            if experiment.source_material_id:
                if experiment.source_material_id not in ctx.source_materials:
                    raise CommandError(
                        f"Experiment '{experiment.name}' references source_material_id="
                        f"{experiment.source_material_id} not found in source team."
                    )
                new_exp.source_material = ctx.source_materials[experiment.source_material_id]

            if experiment.consent_form_id:
                if experiment.consent_form_id not in ctx.consent_forms:
                    raise CommandError(
                        f"Experiment '{experiment.name}' references consent_form_id="
                        f"{experiment.consent_form_id} not found in source team."
                    )
                new_exp.consent_form = ctx.consent_forms[experiment.consent_form_id]

            if experiment.pre_survey_id:
                if experiment.pre_survey_id not in ctx.surveys:
                    raise CommandError(
                        f"Experiment '{experiment.name}' references pre_survey_id="
                        f"{experiment.pre_survey_id} not found in source team."
                    )
                new_exp.pre_survey = ctx.surveys[experiment.pre_survey_id]

            if experiment.post_survey_id:
                if experiment.post_survey_id not in ctx.surveys:
                    raise CommandError(
                        f"Experiment '{experiment.name}' references post_survey_id="
                        f"{experiment.post_survey_id} not found in source team."
                    )
                new_exp.post_survey = ctx.surveys[experiment.post_survey_id]

            if experiment.voice_provider_id:
                if experiment.voice_provider_id not in ctx.voice_providers:
                    raise CommandError(
                        f"Experiment '{experiment.name}' references voice_provider_id="
                        f"{experiment.voice_provider_id} not found in source team."
                    )
                new_exp.voice_provider = ctx.voice_providers[experiment.voice_provider_id]

            if experiment.trace_provider_id:
                if experiment.trace_provider_id not in ctx.trace_providers:
                    raise CommandError(
                        f"Experiment '{experiment.name}' references trace_provider_id="
                        f"{experiment.trace_provider_id} not found in source team."
                    )
                new_exp.trace_provider = ctx.trace_providers[experiment.trace_provider_id]

            new_exp.save()

            # Update the copied pipeline's team and remap its node params
            if new_exp.pipeline:
                new_exp.pipeline.team = ctx.target_team
                new_exp.pipeline.save(update_fields=["team"])

                # Track the mapping for orphan pipeline check
                if experiment.pipeline_id:
                    ctx.pipelines[experiment.pipeline_id] = new_exp.pipeline

                # Remap node params
                for node in new_exp.pipeline.node_set.all():
                    self._remap_node_params(ctx, node)

            # Create initial published version
            new_exp.create_new_version("Initial", make_default=True)

            ctx.experiments[experiment.id] = new_exp

    def _clone_evaluations(self, ctx: CloneContext):
        """Clone evaluators, datasets, and configs."""
        # Evaluators - remap llm_provider_id and llm_provider_model_id in params
        for evaluator in Evaluator.objects.filter(team=ctx.source_team):
            params = dict(evaluator.params)
            self._remap_evaluator_params(ctx, evaluator.name, params)

            new_evaluator = Evaluator.objects.create(
                team=ctx.target_team,
                name=evaluator.name,
                type=evaluator.type,
                params=params,
            )
            ctx.evaluators[evaluator.id] = new_evaluator

        # Evaluation Datasets with M2M messages
        for dataset in EvaluationDataset.objects.filter(team=ctx.source_team):
            new_dataset = EvaluationDataset.objects.create(
                team=ctx.target_team,
                name=dataset.name,
                status=dataset.status,
            )

            # Clone messages (not team-scoped, so we create new copies)
            new_messages = []
            for msg in dataset.messages.all():
                new_msg = EvaluationMessage.objects.create(
                    input=msg.input,
                    output=msg.output,
                    context=msg.context,
                    history=msg.history,
                    participant_data=msg.participant_data,
                    session_state=msg.session_state,
                    metadata=msg.metadata,
                    # input_chat_message and expected_output_chat_message are not cloned
                    # as they reference chat messages from the source team
                )
                new_messages.append(new_msg)

            if new_messages:
                new_dataset.messages.set(new_messages)

            ctx.datasets[dataset.id] = new_dataset

        # Evaluation Configs with FK remapping
        for config in EvaluationConfig.objects.filter(team=ctx.source_team):
            # Dataset is required - error if not found
            if config.dataset_id not in ctx.datasets:
                raise CommandError(
                    f"EvaluationConfig '{config.name}' references dataset_id={config.dataset_id} "
                    f"not found in source team."
                )

            new_config = EvaluationConfig.objects.create(
                team=ctx.target_team,
                name=config.name,
                dataset=ctx.datasets[config.dataset_id],
                version_selection_type=config.version_selection_type,
            )

            # Remap experiment references - error if not found
            if config.experiment_version_id:
                if config.experiment_version_id not in ctx.experiments:
                    raise CommandError(
                        f"EvaluationConfig '{config.name}' references experiment_version_id="
                        f"{config.experiment_version_id} not found in source team."
                    )
                new_config.experiment_version = ctx.experiments[config.experiment_version_id]

            if config.base_experiment_id:
                if config.base_experiment_id not in ctx.experiments:
                    raise CommandError(
                        f"EvaluationConfig '{config.name}' references base_experiment_id="
                        f"{config.base_experiment_id} not found in source team."
                    )
                new_config.base_experiment = ctx.experiments[config.base_experiment_id]

            new_config.save()

            # Remap M2M evaluators - only include evaluators from source team
            new_evaluator_ids = []
            for evaluator in config.evaluators.filter(team=ctx.source_team):
                if evaluator.id not in ctx.evaluators:
                    raise CommandError(
                        f"EvaluationConfig '{config.name}' references evaluator_id={evaluator.id} "
                        f"not found in cloned evaluators."
                    )
                new_evaluator_ids.append(ctx.evaluators[evaluator.id].id)
            if new_evaluator_ids:
                new_config.evaluators.set(new_evaluator_ids)

    def _remap_evaluator_params(self, ctx: CloneContext, name: str, params: dict):
        """Remap FK IDs in evaluator params to new team's objects."""
        # Remap llm_provider_id
        if "llm_provider_id" in params and params["llm_provider_id"]:
            old_id = int(params["llm_provider_id"])
            if old_id in ctx.llm_providers:
                params["llm_provider_id"] = ctx.llm_providers[old_id].id
            elif not LlmProvider.objects.filter(id=old_id, team__isnull=True).exists():
                raise CommandError(
                    f"Evaluator '{name}' references llm_provider_id={old_id} which was not found in source team."
                )
            # else: global provider, leave as-is

        # Remap llm_provider_model_id
        if "llm_provider_model_id" in params and params["llm_provider_model_id"]:
            old_id = int(params["llm_provider_model_id"])
            if old_id in ctx.llm_provider_models:
                params["llm_provider_model_id"] = ctx.llm_provider_models[old_id].id
            elif not LlmProviderModel.objects.filter(id=old_id, team__isnull=True).exists():
                raise CommandError(
                    f"Evaluator '{name}' references llm_provider_model_id={old_id} which was not found in source team."
                )
            # else: global model, leave as-is
