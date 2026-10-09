from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from apps.experiments.models import Experiment
from apps.service_providers.models import LlmProvider
from apps.teams.backends import SUPER_ADMIN_GROUP
from apps.teams.models import Membership, Team
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamFactory


@pytest.fixture(autouse=True)
def _debug_on(settings):
    settings.DEBUG = True
    settings.IS_CI = False


def _run_command():
    call_command(
        "setup_e2e_team",
        email="e2e@example.com",
        password="secret",
        team_slug="e2e-core",
        team_name="E2E Core",
        stdout=StringIO(),
    )


@pytest.mark.django_db()
def test_creates_team_with_single_owner_and_nothing_else():
    _run_command()

    team = Team.objects.get(slug="e2e-core")
    user = get_user_model().objects.get(email="e2e@example.com")
    assert user.check_password("secret")
    membership = Membership.objects.get(team=team)
    assert membership.user == user
    assert list(membership.groups.values_list("name", flat=True)) == [SUPER_ADMIN_GROUP]
    assert not LlmProvider.objects.filter(team=team).exists()
    assert not Experiment.objects.filter(team=team).exists()


@pytest.mark.django_db()
def test_rerun_replaces_existing_team_data():
    _run_command()
    old_team = Team.objects.get(slug="e2e-core")
    LlmProviderFactory.create(team=old_team)
    ExperimentFactory.create(team=old_team)

    _run_command()

    team = Team.objects.get(slug="e2e-core")
    assert team.id != old_team.id
    assert Membership.objects.filter(team=team).count() == 1
    assert not LlmProvider.objects.filter(team=team).exists()
    assert not Experiment.objects.filter(team=team).exists()


@pytest.mark.django_db()
def test_rerun_deletes_teams_the_user_created():
    _run_command()
    user = get_user_model().objects.get(email="e2e@example.com")
    created_by_user = TeamFactory.create(created_by=user)
    created_by_someone_else = TeamFactory.create()

    _run_command()

    assert not Team.objects.filter(id=created_by_user.id).exists()
    assert Team.objects.filter(id=created_by_someone_else.id).exists()


@pytest.mark.parametrize(
    ("debug", "is_ci"),
    [
        pytest.param(True, False, id="debug"),
        pytest.param(False, True, id="ci"),
    ],
)
@pytest.mark.django_db()
def test_runs_in_debug_or_ci(settings, debug, is_ci):
    settings.DEBUG = debug
    settings.IS_CI = is_ci

    _run_command()

    assert Team.objects.filter(slug="e2e-core").exists()


@pytest.mark.django_db()
def test_refuses_to_run_outside_debug_and_ci(settings):
    settings.DEBUG = False
    settings.IS_CI = False
    team = TeamFactory.create(slug="e2e-core")

    with pytest.raises(CommandError, match="DEBUG or CI"):
        _run_command()

    assert Team.objects.filter(id=team.id).exists()


@pytest.mark.django_db()
def test_refuses_to_reuse_a_user_from_another_team():
    other_team = TeamFactory.create()
    user = get_user_model().objects.create_user(username="e2e@example.com", email="e2e@example.com", password="real")
    Membership.objects.create(team=other_team, user=user)

    with pytest.raises(CommandError, match="belongs to other teams"):
        _run_command()

    user.refresh_from_db()
    assert user.check_password("real")
    assert not Team.objects.filter(slug="e2e-core").exists()
