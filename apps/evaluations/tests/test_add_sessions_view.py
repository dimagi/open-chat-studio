from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.evaluations.models import EvaluationDataset, EvaluationMode
from apps.utils.factories.experiment import ExperimentSessionFactory
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


@pytest.mark.django_db()
def test_post_reads_filter_params_from_post_body(client_with_user, team_with_users, message_dataset):
    """Regression: the view used FilterParams.from_request (GET) so POSTed filter params
    were silently dropped. The clone task must receive the actual filter query."""
    session = ExperimentSessionFactory.create(team=team_with_users)
    url = _add_sessions_url(team_with_users, message_dataset)
    with patch("apps.evaluations.dataset_clone.create_dataset_from_session_messages_task.delay") as mock_delay:
        mock_delay.return_value.id = "test-task-id"
        response = client_with_user.post(
            url,
            {
                "mode": "selected",
                "session_ids": str(session.external_id),
                "message_scope": "filtered",
                "f_tags": "+1",
                "op_tags": "any of",
            },
        )
    assert response.status_code == 302
    mock_delay.assert_called_once()
    # 5th positional arg is the filter_query string passed to the Celery task.
    filter_query = mock_delay.call_args.args[4]
    assert filter_query, "filter_query should be populated from POST body, not empty"
    assert "f_tags=%2B1" in filter_query
    assert "op_tags=any" in filter_query


@pytest.mark.django_db()
def test_session_mode_all_matching_passes_filter_instead_of_ids(client_with_user, team_with_users, session_dataset):
    """Session-mode "all matching" hands the filter to the task rather than resolving 10k UUIDs
    into the Celery message."""
    session = ExperimentSessionFactory.create(team=team_with_users)
    ExperimentSessionFactory.create(team=team_with_users)  # different experiment, excluded

    with patch("apps.evaluations.dataset_clone.create_dataset_from_sessions_task.delay") as mock_delay:
        mock_delay.return_value.id = "test-task-id"
        response = client_with_user.post(
            _add_sessions_url(team_with_users, session_dataset),
            {
                "mode": "all_matching",
                "session_ids": "",
                "f_experiment": str(session.experiment_id),
                "op_experiment": "any of",
            },
        )

    assert response.status_code == 302
    mock_delay.assert_called_once()
    _dataset_id, _team_id, session_ids, filter_query, _tz = mock_delay.call_args.args
    assert session_ids is None
    assert f"f_experiment={session.experiment_id}" in filter_query


@pytest.mark.django_db()
def test_session_mode_selected_still_passes_ids(client_with_user, team_with_users, session_dataset):
    """The hand-picked path is unchanged: explicit ids, no filter."""
    session = ExperimentSessionFactory.create(team=team_with_users)

    with patch("apps.evaluations.dataset_clone.create_dataset_from_sessions_task.delay") as mock_delay:
        mock_delay.return_value.id = "test-task-id"
        response = client_with_user.post(
            _add_sessions_url(team_with_users, session_dataset),
            {"mode": "selected", "session_ids": str(session.external_id)},
        )

    assert response.status_code == 302
    _dataset_id, _team_id, session_ids, filter_query, _tz = mock_delay.call_args.args
    assert session_ids == [str(session.external_id)]
    assert filter_query is None


@pytest.mark.django_db()
def test_message_mode_all_matching_over_limit_is_rejected(client_with_user, team_with_users, message_dataset):
    """Message mode has to resolve "all matching" to ids, so the same cap applies here as on the
    create form — the dataset must not be marked pending or dispatched."""
    ExperimentSessionFactory.create_batch(2, team=team_with_users)

    with (
        patch("apps.evaluations.dataset_clone.MESSAGE_MODE_ALL_MATCHING_LIMIT", 1),
        patch("apps.evaluations.dataset_clone.create_dataset_from_session_messages_task.delay") as mock_delay,
    ):
        response = client_with_user.post(
            _add_sessions_url(team_with_users, message_dataset),
            {"mode": "all_matching", "session_ids": ""},
            follow=True,
        )

    mock_delay.assert_not_called()
    assert any("limited to 1 session" in str(m) for m in response.context["messages"])
