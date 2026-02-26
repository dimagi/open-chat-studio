from io import StringIO

import pytest
from allauth.account.models import EmailAddress
from django.core.management import call_command

from apps.evaluations.models import EvaluationConfig, EvaluationDataset, Evaluator
from apps.experiments.models import ConsentForm, Experiment, SourceMaterial, Survey
from apps.pipelines.models import Node, Pipeline
from apps.service_providers.models import LlmProvider, LlmProviderModel, TraceProvider, VoiceProvider
from apps.teams.models import Flag, Membership, Team
from apps.users.models import CustomUser
from apps.utils.factories.evaluations import EvaluationConfigFactory, EvaluationDatasetFactory, EvaluatorFactory
from apps.utils.factories.experiment import ConsentFormFactory, SourceMaterialFactory, SurveyFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    LlmProviderModelFactory,
    TraceProviderFactory,
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


@pytest.fixture(scope="module")
def source_team(django_db_blocker):
    """Create a source team with various related data."""
    from apps.teams.utils import current_team
    from apps.utils.deletion import delete_object_with_auditing_of_related_objects

    with django_db_blocker.unblock():
        # Clean up any leftover from previous runs
        existing = Team.objects.filter(slug="source-team").first()
        if existing:
            with current_team(existing):
                delete_object_with_auditing_of_related_objects(existing)

        team = TeamFactory(name="Source Team", slug="source-team")
        owner = UserFactory()

        # Providers
        llm_provider = LlmProviderFactory(team=team)
        llm_model = LlmProviderModelFactory(team=team)
        voice_provider = VoiceProviderFactory(team=team)
        trace_provider = TraceProviderFactory(team=team)

        # Content
        source_material = SourceMaterialFactory(team=team)
        consent_form = ConsentFormFactory(team=team)
        survey = SurveyFactory(team=team)

        # Pipeline
        pipeline = Pipeline.create_default(team, "Test Pipeline", llm_provider.id, llm_model)

        # Experiment with FKs - use direct creation to avoid SyntheticVoice issue
        experiment = Experiment.objects.create(
            team=team,
            owner=owner,
            name="Test Experiment",
            source_material=source_material,
            consent_form=consent_form,
            pre_survey=survey,
            voice_provider=voice_provider,
            trace_provider=trace_provider,
            pipeline=pipeline,
        )

        # Evaluations
        evaluator = EvaluatorFactory(team=team)
        dataset = EvaluationDatasetFactory(team=team)
        EvaluationConfigFactory(team=team, dataset=dataset, base_experiment=experiment, evaluators=[evaluator])

        yield team

        # Cleanup after all tests in module
        with current_team(team):
            delete_object_with_auditing_of_related_objects(team)


@pytest.mark.django_db()
def test_clone_team_nonexistent_source():
    """Test error when source team doesn't exist."""
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="does not exist"):
        call_command(
            "clone_team",
            "--source-team=nonexistent",
            "--count=1",
            "--name-template=test_{n}",
            "--email-template=test{n}@example.com",
            "--password-template=pass{n}",
        )


@pytest.mark.django_db()
def test_clone_team_dry_run(source_team):
    """Test dry run mode shows preview without creating."""
    out = StringIO()

    call_command(
        "clone_team",
        f"--source-team={source_team.slug}",
        "--count=2",
        "--name-template=test_{n}",
        "--email-template=test{n}@example.com",
        "--password-template=pass{n}",
        "--dry-run",
        stdout=out,
    )

    output = out.getvalue()
    assert "DRY RUN MODE" in output
    assert "Would create" in output
    assert "test_1" in output
    assert "test_2" in output

    # Verify nothing was created
    assert not Team.objects.filter(slug="test_1").exists()
    assert not Team.objects.filter(slug="test_2").exists()


@pytest.mark.django_db()
def test_clone_team_creates_team_and_user(source_team):
    """Test basic team and user creation."""
    out = StringIO()
    call_command(
        "clone_team",
        f"--source-team={source_team.slug}",
        "--count=1",
        "--name-template=client_{n}",
        "--email-template=client{n}@example.com",
        "--password-template=password{n}",
        "--force",
        stdout=out,
    )
    output = out.getvalue()
    assert "Failed: 0" in output, f"Clone failed: {output}"

    # Verify team created
    target = Team.objects.get(slug="client_1")
    assert target.name == "client_1"

    # Verify user created with verified email
    user = CustomUser.objects.get(email="client1@example.com")
    assert user.check_password("password1")
    assert EmailAddress.objects.filter(user=user, email="client1@example.com", verified=True).exists()

    # Verify membership with owner role
    membership = Membership.objects.get(team=target, user=user)
    assert membership.is_team_admin()


@pytest.mark.django_db()
def test_clone_team_clones_providers(source_team):
    """Test providers are cloned to new team."""
    call_command(
        "clone_team",
        f"--source-team={source_team.slug}",
        "--count=1",
        "--name-template=target_{n}",
        "--email-template=target{n}@example.com",
        "--password-template=pass{n}",
        "--force",
        stdout=StringIO(),
    )

    target = Team.objects.get(slug="target_1")

    # Verify providers cloned
    assert LlmProvider.objects.filter(team=target).count() == LlmProvider.objects.filter(team=source_team).count()
    source_model_count = LlmProviderModel.objects.filter(team=source_team).count()
    assert LlmProviderModel.objects.filter(team=target).count() == source_model_count
    assert VoiceProvider.objects.filter(team=target).count() == VoiceProvider.objects.filter(team=source_team).count()
    assert TraceProvider.objects.filter(team=target).count() == TraceProvider.objects.filter(team=source_team).count()


@pytest.mark.django_db()
def test_clone_team_clones_content(source_team):
    """Test versioned content models are cloned."""
    call_command(
        "clone_team",
        f"--source-team={source_team.slug}",
        "--count=1",
        "--name-template=target_{n}",
        "--email-template=target{n}@example.com",
        "--password-template=pass{n}",
        "--force",
        stdout=StringIO(),
    )

    target = Team.objects.get(slug="target_1")

    # Verify content cloned
    source_sm_count = SourceMaterial.objects.working_versions_queryset().filter(team=source_team).count()
    target_sm_count = SourceMaterial.objects.working_versions_queryset().filter(team=target).count()
    assert target_sm_count == source_sm_count

    source_cf_count = ConsentForm.objects.working_versions_queryset().filter(team=source_team).count()
    target_cf_count = ConsentForm.objects.working_versions_queryset().filter(team=target).count()
    assert target_cf_count == source_cf_count

    source_s_count = Survey.objects.working_versions_queryset().filter(team=source_team).count()
    target_s_count = Survey.objects.working_versions_queryset().filter(team=target).count()
    assert target_s_count == source_s_count


@pytest.mark.django_db()
def test_clone_team_clones_experiments_with_remapped_fks(source_team):
    """Test experiments are cloned with FK relationships remapped."""
    call_command(
        "clone_team",
        f"--source-team={source_team.slug}",
        "--count=1",
        "--name-template=target_{n}",
        "--email-template=target{n}@example.com",
        "--password-template=pass{n}",
        "--force",
        stdout=StringIO(),
    )

    target = Team.objects.get(slug="target_1")
    target_exp = Experiment.objects.filter(team=target).first()
    source_exp = Experiment.objects.filter(team=source_team).first()

    # Verify experiment cloned with same name (no _copy suffix)
    assert target_exp is not None
    assert target_exp.name == source_exp.name

    # Verify FKs remapped to new team's objects
    if source_exp.source_material:
        assert target_exp.source_material is not None
        assert target_exp.source_material.team == target
        assert target_exp.source_material.id != source_exp.source_material.id

    if source_exp.consent_form:
        assert target_exp.consent_form is not None
        assert target_exp.consent_form.team == target

    if source_exp.pipeline:
        assert target_exp.pipeline is not None
        assert target_exp.pipeline.team == target


@pytest.mark.django_db()
def test_clone_team_clones_evaluations(source_team):
    """Test evaluations are cloned with remapped references."""
    call_command(
        "clone_team",
        f"--source-team={source_team.slug}",
        "--count=1",
        "--name-template=target_{n}",
        "--email-template=target{n}@example.com",
        "--password-template=pass{n}",
        "--force",
        stdout=StringIO(),
    )

    target = Team.objects.get(slug="target_1")

    # Verify evaluators cloned
    source_eval_count = Evaluator.objects.filter(team=source_team).count()
    target_eval_count = Evaluator.objects.filter(team=target).count()
    assert target_eval_count == source_eval_count

    # Verify datasets cloned
    source_ds_count = EvaluationDataset.objects.filter(team=source_team).count()
    target_ds_count = EvaluationDataset.objects.filter(team=target).count()
    assert target_ds_count == source_ds_count

    # Verify configs cloned
    source_cfg_count = EvaluationConfig.objects.filter(team=source_team).count()
    target_cfg_count = EvaluationConfig.objects.filter(team=target).count()
    assert target_cfg_count == source_cfg_count


@pytest.mark.django_db()
def test_clone_team_multiple_teams(source_team):
    """Test creating multiple teams with start index."""
    call_command(
        "clone_team",
        f"--source-team={source_team.slug}",
        "--count=3",
        "--name-template=demo_{n}",
        "--email-template=demo{n}@example.com",
        "--password-template=pass{n}",
        "--start-index=5",
        "--force",
        stdout=StringIO(),
    )

    # Verify teams created with correct indices
    assert Team.objects.filter(slug="demo_5").exists()
    assert Team.objects.filter(slug="demo_6").exists()
    assert Team.objects.filter(slug="demo_7").exists()

    # Verify emails match indices
    assert CustomUser.objects.filter(email="demo5@example.com").exists()
    assert CustomUser.objects.filter(email="demo6@example.com").exists()
    assert CustomUser.objects.filter(email="demo7@example.com").exists()


@pytest.mark.django_db()
def test_clone_team_clones_pipelines(source_team):
    """Test pipelines are cloned to new team."""
    call_command(
        "clone_team",
        f"--source-team={source_team.slug}",
        "--count=1",
        "--name-template=target_{n}",
        "--email-template=target{n}@example.com",
        "--password-template=pass{n}",
        "--force",
        stdout=StringIO(),
    )

    target = Team.objects.get(slug="target_1")

    # Verify pipelines cloned
    source_pipeline_count = Pipeline.objects.working_versions_queryset().filter(team=source_team).count()
    target_pipeline_count = Pipeline.objects.working_versions_queryset().filter(team=target).count()
    assert target_pipeline_count == source_pipeline_count

    # Verify pipeline belongs to new team
    target_pipeline = Pipeline.objects.filter(team=target).first()
    if target_pipeline:
        assert target_pipeline.team == target


@pytest.mark.django_db()
def test_clone_team_remaps_pipeline_node_params(source_team):
    """Test pipeline node params are remapped to new team's providers."""
    call_command(
        "clone_team",
        f"--source-team={source_team.slug}",
        "--count=1",
        "--name-template=target_{n}",
        "--email-template=target{n}@example.com",
        "--password-template=pass{n}",
        "--force",
        stdout=StringIO(),
    )

    target = Team.objects.get(slug="target_1")
    target_pipeline = Pipeline.objects.filter(team=target).first()
    assert target_pipeline is not None

    # Get the target team's providers
    target_llm_provider = LlmProvider.objects.filter(team=target).first()
    target_llm_model = LlmProviderModel.objects.filter(team=target).first()

    # Get source team's providers for comparison
    source_llm_provider = LlmProvider.objects.filter(team=source_team).first()
    source_llm_model = LlmProviderModel.objects.filter(team=source_team).first()

    # Check nodes have remapped params
    for node in Node.objects.filter(pipeline=target_pipeline):
        params = node.params
        if "llm_provider_id" in params and params["llm_provider_id"]:
            # Should reference target team's provider, not source
            assert params["llm_provider_id"] == target_llm_provider.id
            assert params["llm_provider_id"] != source_llm_provider.id

        if "llm_provider_model_id" in params and params["llm_provider_model_id"]:
            # Should reference target team's model, not source
            assert params["llm_provider_model_id"] == target_llm_model.id
            assert params["llm_provider_model_id"] != source_llm_model.id


@pytest.mark.django_db()
def test_clone_team_copies_feature_flags(source_team):
    """Test target team is added to same feature flags as source team."""
    # Create a feature flag and add source team
    flag, _ = Flag.objects.get_or_create(name="flag_test_clone")
    flag.teams.add(source_team)

    call_command(
        "clone_team",
        f"--source-team={source_team.slug}",
        "--count=1",
        "--name-template=target_{n}",
        "--email-template=target{n}@example.com",
        "--password-template=pass{n}",
        "--force",
        stdout=StringIO(),
    )

    target = Team.objects.get(slug="target_1")

    # Verify target team is in the same flag
    assert flag.teams.filter(id=target.id).exists()
