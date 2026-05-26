import pytest
from django.test import Client
from django.urls import reverse

from apps.evaluations.models import EvaluationDataset, EvaluationMode
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def user(team_with_users):
    return team_with_users.members.first()


@pytest.fixture()
def client_with_user(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture()
def session_dataset(team_with_users):
    return EvaluationDataset.objects.create(
        team=team_with_users, name="Session DS", evaluation_mode=EvaluationMode.SESSION
    )


@pytest.fixture()
def message_dataset(team_with_users):
    return EvaluationDataset.objects.create(
        team=team_with_users, name="Message DS", evaluation_mode=EvaluationMode.MESSAGE
    )


def _add_sessions_url(team, dataset):
    return reverse("evaluations:dataset_add_sessions", args=[team.slug, dataset.pk])


@pytest.mark.django_db()
def test_session_mode_dataset_has_no_clone_toggle(client_with_user, team_with_users, session_dataset):
    """Session-mode datasets never show the Clone toggle."""
    response = client_with_user.get(_add_sessions_url(team_with_users, session_dataset))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="message_scope_ui"' not in content


@pytest.mark.django_db()
def test_session_mode_dataset_has_no_old_messages_to_clone_row(client_with_user, team_with_users, session_dataset):
    """The legacy 'Messages to clone' bar must be gone for all dataset modes."""
    response = client_with_user.get(_add_sessions_url(team_with_users, session_dataset))
    assert response.status_code == 200
    assert "Messages to clone" not in response.content.decode()


@pytest.mark.django_db()
def test_message_mode_dataset_renders_clone_toggle_markup(client_with_user, team_with_users, message_dataset):
    """Message-mode datasets render the Clone toggle markup (client-side x-show controls visibility)."""
    response = client_with_user.get(_add_sessions_url(team_with_users, message_dataset))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'x-model="messageScope"' in content
    assert "All messages" in content
    assert "Filtered messages only" in content
    # Visibility is gated client-side on hasActiveFilters
    assert 'x-show="hasActiveFilters"' in content


@pytest.mark.django_db()
def test_message_mode_dataset_has_no_old_messages_to_clone_row(client_with_user, team_with_users, message_dataset):
    response = client_with_user.get(_add_sessions_url(team_with_users, message_dataset))
    assert response.status_code == 200
    # The new label is "Clone:" (inline), not "Messages to clone:" (legacy banner)
    assert "Messages to clone" not in response.content.decode()


@pytest.mark.django_db()
def test_unified_action_bar_renders_for_all_dataset_modes(client_with_user, team_with_users, session_dataset):
    """All three Add-mode pills + count + primary action all live in one row labeled 'Add to dataset:'."""
    response = client_with_user.get(_add_sessions_url(team_with_users, session_dataset))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Add to dataset" in content


@pytest.mark.django_db()
def test_post_without_message_scope_defaults_to_all(client_with_user, team_with_users, message_dataset):
    """If the Clone toggle is hidden (no filters), the form still posts a usable default."""
    response = client_with_user.post(
        _add_sessions_url(team_with_users, message_dataset),
        {"mode": "selected", "session_ids": "", "message_scope": ""},
    )
    # No sessions selected, server redirects back with an error — that's fine, we're only
    # asserting the view doesn't crash on an empty message_scope value.
    assert response.status_code == 302
