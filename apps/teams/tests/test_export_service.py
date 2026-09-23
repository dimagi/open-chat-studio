import pytest

from apps.events.models import StaticTrigger
from apps.teams.export_service import frozen_experiment_q
from apps.utils.factories.events import StaticTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory

pytestmark = pytest.mark.django_db


def _firing(queryset=None):
    return set((queryset or StaticTrigger.objects).exclude(frozen_experiment_q()).values_list("id", flat=True))


def test_nothing_is_frozen_when_no_team_is_migrating():
    trigger = StaticTriggerFactory(experiment=ExperimentFactory(team=TeamFactory()))
    assert trigger.id in _firing()


def test_a_migrating_team_without_a_selection_freezes_everything():
    team = TeamFactory(is_migrating=True)
    trigger = StaticTriggerFactory(experiment=ExperimentFactory(team=team))
    assert trigger.id not in _firing()


def test_a_selection_freezes_only_the_selected_chatbots():
    team = TeamFactory(is_migrating=True)
    migrating = ExperimentFactory(team=team)
    still_running = ExperimentFactory(team=team)
    team.exportable_experiments.add(migrating)

    frozen = StaticTriggerFactory(experiment=migrating)
    live = StaticTriggerFactory(experiment=still_running)

    firing = _firing()
    assert frozen.id not in firing
    assert live.id in firing


def test_a_trigger_on_a_published_version_is_frozen_with_its_family():
    """Triggers are versioned alongside their chatbot, so a trigger on version 3 points at the
    version-3 row, not the working version the allowlist holds."""
    team = TeamFactory(is_migrating=True)
    working = ExperimentFactory(team=team)
    published = ExperimentFactory(team=team, working_version=working)
    team.exportable_experiments.add(working)

    trigger = StaticTriggerFactory(experiment=published)
    assert trigger.id not in _firing()


def test_a_selection_on_a_team_not_migrating_freezes_nothing():
    team = TeamFactory(is_migrating=False)
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)

    trigger = StaticTriggerFactory(experiment=chatbot)
    assert trigger.id in _firing()


def test_frozen_experiment_q_issues_no_extra_query(django_assert_num_queries):
    """Both sides are lazy subqueries, so building the Q costs nothing and running it is one
    statement -- it lands on paths that run thousands of times a day."""
    TeamFactory(is_migrating=True)
    with django_assert_num_queries(0):
        frozen_experiment_q()
    with django_assert_num_queries(1):
        list(StaticTrigger.objects.exclude(frozen_experiment_q()).values_list("id", flat=True))
