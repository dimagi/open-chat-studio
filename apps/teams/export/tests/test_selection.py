import pytest

from apps.teams.export.selection import (
    expand_to_family,
    migrating_chatbot_count,
    selectable_chatbots,
    selected_chatbots,
    selected_experiment_ids,
)
from apps.teams.models import Team
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory

pytestmark = pytest.mark.django_db


def test_selected_experiment_ids_is_empty_by_default():
    assert selected_experiment_ids(TeamFactory()) == []


def test_selected_experiment_ids_returns_the_allowlist():
    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)
    assert selected_experiment_ids(team) == [chatbot.id]


def test_selected_experiment_ids_includes_an_archived_chatbot():
    """A chatbot archived after being selected stays selected and stays exportable."""
    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)
    chatbot.is_archived = True
    chatbot.save()
    assert selected_experiment_ids(team) == [chatbot.id]


def test_the_allowlist_ignores_another_teams_chatbot():
    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot, ExperimentFactory())
    assert selected_experiment_ids(team) == [chatbot.id]
    assert list(selected_chatbots(team)) == [chatbot]


def test_expand_to_family_includes_published_and_archived_versions():
    """Selecting a chatbot selects the whole family. The published copies carry a self-referential
    working_version FK, so exporting one without its working version leaves an unresolvable link."""
    team = TeamFactory()
    working = ExperimentFactory(team=team)
    published = ExperimentFactory(team=team, working_version=working, version_number=1)
    archived = ExperimentFactory(team=team, working_version=working, version_number=2, is_archived=True)
    unrelated = ExperimentFactory(team=team)

    family = set(expand_to_family([working.id]).values_list("id", flat=True))

    assert family == {working.id, published.id, archived.id}
    assert unrelated.id not in family


def test_expand_to_family_of_nothing_is_empty():
    ExperimentFactory()
    assert list(expand_to_family([])) == []


def test_selectable_chatbots_offers_working_chatbots_only():
    team = TeamFactory()
    working = ExperimentFactory(team=team)
    ExperimentFactory(team=team, working_version=working)
    ExperimentFactory()  # another team's

    assert set(selectable_chatbots(team).values_list("id", flat=True)) == {working.id}


def test_selectable_chatbots_keeps_an_already_selected_archived_chatbot():
    """working_chatbots() filters archived rows out of the picker, not out of the allowlist. Without
    this union the archived chatbot fails the form's queryset validation and is dropped on save."""
    team = TeamFactory()
    archived = ExperimentFactory(team=team, is_archived=True)
    team.exportable_experiments.add(archived)

    assert archived.id in set(selectable_chatbots(team).values_list("id", flat=True))


def test_selectable_chatbots_omits_an_archived_chatbot_that_was_never_selected():
    team = TeamFactory()
    ExperimentFactory(team=team, is_archived=True)
    assert list(selectable_chatbots(team)) == []


def test_migrating_chatbot_count_is_zero_when_not_migrating(django_assert_num_queries):
    team = TeamFactory(is_migrating=False)
    team.exportable_experiments.add(ExperimentFactory(team=team))
    with django_assert_num_queries(0):
        assert migrating_chatbot_count(team) == 0


def test_migrating_chatbot_count_is_cached(django_assert_num_queries):
    team = TeamFactory(is_migrating=True)
    team.exportable_experiments.add(ExperimentFactory(team=team))
    assert migrating_chatbot_count(team) == 1
    with django_assert_num_queries(0):
        assert migrating_chatbot_count(team) == 1


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        pytest.param(lambda team, kept, other: team.exportable_experiments.add(other), 2, id="add"),
        pytest.param(lambda team, kept, other: team.exportable_experiments.remove(kept), 0, id="remove"),
        pytest.param(lambda team, kept, other: team.exportable_experiments.clear(), 0, id="clear"),
        pytest.param(lambda team, kept, other: team.exportable_experiments.set([other]), 1, id="set"),
        pytest.param(lambda team, kept, other: kept.delete(), 0, id="chatbot-deleted"),
    ],
)
def test_migrating_chatbot_count_refreshes_when_the_allowlist_changes(change, expected):
    team = TeamFactory(is_migrating=True)
    kept = ExperimentFactory(team=team)
    other = ExperimentFactory(team=team)
    team.exportable_experiments.add(kept)
    assert migrating_chatbot_count(team) == 1

    change(team, kept, other)

    assert migrating_chatbot_count(team) == expected


def test_migrating_chatbot_count_refreshes_after_migration_mode_is_turned_off():
    team = TeamFactory(is_migrating=True)
    assert migrating_chatbot_count(team) == 0
    # bulk_create sends no signals, so only the migration-mode toggle can clear the cached value.
    Team.exportable_experiments.through.objects.bulk_create(
        [Team.exportable_experiments.through(team=team, experiment=ExperimentFactory(team=team))]
    )

    team.is_migrating = False
    team.save()
    team.is_migrating = True
    team.save()

    assert migrating_chatbot_count(team) == 1
