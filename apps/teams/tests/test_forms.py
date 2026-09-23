import pytest
from field_audit import enable_audit
from field_audit.models import AuditEvent

from apps.teams.export.selection import selected_experiment_ids
from apps.teams.forms import EXPORT_SCOPE_ALL, EXPORT_SCOPE_SELECTED, TeamPublicKeyForm
from apps.teams.models import Team
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory

pytestmark = pytest.mark.django_db


def _post(team, **overrides):
    data = {"public_key": "", "export_scope": EXPORT_SCOPE_ALL}
    data.update(overrides)
    return TeamPublicKeyForm(data, instance=team)


def test_all_chatbots_clears_the_allowlist():
    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)

    form = _post(team, export_scope=EXPORT_SCOPE_ALL, exportable_experiments=[chatbot.id])
    assert form.is_valid(), form.errors
    form.save()

    assert list(team.exportable_experiments.all()) == []


def test_only_selected_saves_the_picked_chatbots():
    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)

    form = _post(team, export_scope=EXPORT_SCOPE_SELECTED, exportable_experiments=[chatbot.id])
    assert form.is_valid(), form.errors
    form.save()

    assert list(team.exportable_experiments.all()) == [chatbot]


def test_only_selected_with_nothing_picked_is_an_error():
    team = TeamFactory()
    form = _post(team, export_scope=EXPORT_SCOPE_SELECTED, exportable_experiments=[])
    assert not form.is_valid()
    assert "Pick at least one chatbot" in str(form.errors["exportable_experiments"])


def test_a_published_version_cannot_be_submitted():
    team = TeamFactory()
    working = ExperimentFactory(team=team)
    published = ExperimentFactory(team=team, working_version=working)

    form = _post(team, export_scope=EXPORT_SCOPE_SELECTED, exportable_experiments=[published.id])
    assert not form.is_valid()


def test_another_teams_chatbot_cannot_be_submitted():
    team = TeamFactory()
    theirs = ExperimentFactory()

    form = _post(team, export_scope=EXPORT_SCOPE_SELECTED, exportable_experiments=[theirs.id])
    assert not form.is_valid()


def test_initial_scope_follows_the_saved_allowlist():
    team = TeamFactory()
    assert TeamPublicKeyForm(instance=team).initial["export_scope"] == EXPORT_SCOPE_ALL

    team.exportable_experiments.add(ExperimentFactory(team=team))
    assert TeamPublicKeyForm(instance=team).initial["export_scope"] == EXPORT_SCOPE_SELECTED


def test_all_chatbots_clears_an_archived_selection():
    """Experiment's default manager hides archived rows, so clearing through it would leave an
    archived chatbot in the allowlist."""
    team = TeamFactory()
    archived = ExperimentFactory(team=team, is_archived=True)
    team.exportable_experiments.add(archived)

    form = _post(team, export_scope=EXPORT_SCOPE_ALL)
    assert form.is_valid(), form.errors
    form.save()

    assert selected_experiment_ids(team) == []


def test_saving_keeps_an_archived_selection():
    team = TeamFactory()
    archived = ExperimentFactory(team=team, is_archived=True)
    live = ExperimentFactory(team=team)
    team.exportable_experiments.add(archived)

    form = _post(team, export_scope=EXPORT_SCOPE_SELECTED, exportable_experiments=[archived.id, live.id])
    assert form.is_valid(), form.errors
    form.save()

    assert sorted(selected_experiment_ids(team)) == sorted([archived.id, live.id])


def test_initial_scope_counts_an_archived_selection():
    team = TeamFactory()
    team.exportable_experiments.add(ExperimentFactory(team=team, is_archived=True))
    assert TeamPublicKeyForm(instance=team).initial["export_scope"] == EXPORT_SCOPE_SELECTED


def test_saving_the_allowlist_is_audited():
    with enable_audit():
        team = TeamFactory()
        chatbot = ExperimentFactory(team=team)
        form = _post(team, export_scope=EXPORT_SCOPE_SELECTED, exportable_experiments=[chatbot.id])
        assert form.is_valid(), form.errors
        form.save()

    events = AuditEvent.objects.by_model(Team).filter(object_pk=team.id)
    assert any("exportable_experiments" in (e.delta or {}) for e in events)
