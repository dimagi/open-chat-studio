from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.evaluations.models import DatasetCreationStatus, EvaluationDataset
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def client_with_user(team_with_users):
    client = Client()
    client.force_login(team_with_users.members.first())
    return client


def _dataset_new_url(team):
    return reverse("evaluations:dataset_new", args=[team.slug])


@pytest.mark.django_db()
def test_get_with_new_format_filters_selects_all_matching_scope(client_with_user, team_with_users):
    """Arriving at "Create dataset" with new-format (f_/op_) filters selects every matching
    session — by storing the scope, not by inlining every session id into the form."""
    matching_exp = ExperimentFactory.create(team=team_with_users)
    matching = ExperimentSessionFactory.create(team=team_with_users, experiment=matching_exp)

    response = client_with_user.get(
        _dataset_new_url(team_with_users),
        {"f_experiment": str(matching_exp.id), "op_experiment": "any of"},
    )

    assert response.status_code == 200
    assert response.context["form"].initial.get("session_scope") == "all_matching"
    # The session ids themselves never reach the page — that hidden input was ~390 KB at 10k.
    assert not response.context["form"].initial.get("session_ids")
    assert str(matching.external_id) not in response.content.decode()


@pytest.mark.django_db()
def test_get_without_filters_defaults_to_selected_scope(client_with_user, team_with_users):
    """Without explicit filter params in the URL, nothing is pre-selected."""
    ExperimentSessionFactory.create(team=team_with_users)

    response = client_with_user.get(_dataset_new_url(team_with_users))

    assert response.status_code == 200
    assert not response.context["form"].initial.get("session_scope")
    assert not response.context["form"].initial.get("session_ids")


@pytest.mark.django_db()
def test_post_all_matching_session_mode_passes_filter_not_ids(client_with_user, team_with_users):
    """Session-mode "all matching" hands the task the filter; the ids are never enumerated."""
    matching_exp = ExperimentFactory.create(team=team_with_users)
    ExperimentSessionFactory.create(team=team_with_users, experiment=matching_exp)
    ExperimentSessionFactory.create(team=team_with_users)  # excluded by the filter

    url = f"{_dataset_new_url(team_with_users)}?f_experiment={matching_exp.id}&op_experiment=any+of"
    with patch("apps.evaluations.forms.create_dataset_from_sessions_task.delay") as mock_delay:
        mock_delay.return_value.id = "task-id"
        response = client_with_user.post(
            url,
            {
                "name": "All matching session dataset",
                "evaluation_mode": "session",
                "mode": "clone",
                "session_ids": "",
                "session_scope": "all_matching",
            },
        )

    assert response.status_code == 302, getattr(response, "context", {}) and response.context["form"].errors
    mock_delay.assert_called_once()
    _dataset_id, _team_id, session_ids, filter_query, _tz = mock_delay.call_args.args
    assert session_ids is None
    assert f"f_experiment={matching_exp.id}" in filter_query


@pytest.mark.django_db()
def test_post_all_matching_message_mode_resolves_ids_server_side(client_with_user, team_with_users):
    """Message-mode still needs ids, but resolves them server-side instead of via the browser."""
    matching_exp = ExperimentFactory.create(team=team_with_users)
    matching = ExperimentSessionFactory.create(team=team_with_users, experiment=matching_exp)
    other = ExperimentSessionFactory.create(team=team_with_users)

    url = f"{_dataset_new_url(team_with_users)}?f_experiment={matching_exp.id}&op_experiment=any+of"
    with patch("apps.evaluations.forms.create_dataset_from_session_messages_task.delay") as mock_delay:
        mock_delay.return_value.id = "task-id"
        response = client_with_user.post(
            url,
            {
                "name": "All matching message dataset",
                "evaluation_mode": "message",
                "mode": "clone",
                "session_ids": "",
                "session_scope": "all_matching",
            },
        )

    assert response.status_code == 302
    mock_delay.assert_called_once()
    session_ids = mock_delay.call_args.args[2]
    assert session_ids == [str(matching.external_id)]
    assert str(other.external_id) not in session_ids


@pytest.mark.django_db()
@pytest.mark.parametrize("evaluation_mode", ["message", "session"])
def test_post_all_matching_with_no_matches_creates_empty_dataset(client_with_user, team_with_users, evaluation_mode):
    """Either mode may be created empty and filled later, from the Add Sessions page or (for
    session mode) by an auto-population rule. No job runs, so the dataset is closed out here."""
    empty_exp = ExperimentFactory.create(team=team_with_users)

    url = f"{_dataset_new_url(team_with_users)}?f_experiment={empty_exp.id}&op_experiment=any+of"
    response = client_with_user.post(
        url,
        {
            "name": "No matches",
            "evaluation_mode": evaluation_mode,
            "mode": "clone",
            "session_ids": "",
            "session_scope": "all_matching",
        },
    )

    assert response.status_code == 302, response.context["form"].errors
    dataset = EvaluationDataset.objects.get(team=team_with_users, name="No matches")
    assert dataset.messages.count() == 0
    assert dataset.status == DatasetCreationStatus.COMPLETED
    assert not dataset.job_id


@pytest.mark.django_db()
@pytest.mark.parametrize("evaluation_mode", ["message", "session"])
def test_post_selected_scope_with_nothing_selected_creates_empty_dataset(
    client_with_user, team_with_users, evaluation_mode
):
    """Same for the hand-picked scope: submitting with no rows selected starts the dataset empty."""
    response = client_with_user.post(
        _dataset_new_url(team_with_users),
        {
            "name": "Empty to start",
            "evaluation_mode": evaluation_mode,
            "mode": "clone",
            "session_ids": "",
            "session_scope": "selected",
        },
    )

    assert response.status_code == 302, response.context["form"].errors
    dataset = EvaluationDataset.objects.get(team=team_with_users, name="Empty to start")
    assert dataset.messages.count() == 0
    assert dataset.status == DatasetCreationStatus.COMPLETED


@pytest.mark.django_db()
def test_post_all_matching_message_mode_over_limit_is_rejected(client_with_user, team_with_users):
    """Message mode resolves 'all matching' to an explicit id list, so it is capped."""
    matching_exp = ExperimentFactory.create(team=team_with_users)
    ExperimentSessionFactory.create_batch(2, team=team_with_users, experiment=matching_exp)

    url = f"{_dataset_new_url(team_with_users)}?f_experiment={matching_exp.id}&op_experiment=any+of"
    with patch("apps.evaluations.dataset_clone.MESSAGE_MODE_ALL_MATCHING_LIMIT", 1):
        response = client_with_user.post(
            url,
            {
                "name": "Too many",
                "evaluation_mode": "message",
                "mode": "clone",
                "session_ids": "",
                "session_scope": "all_matching",
            },
        )

    assert response.status_code == 200
    assert any("limited to 1 session" in str(e) for e in response.context["form"].errors["__all__"])
    assert not EvaluationDataset.objects.filter(team=team_with_users, name="Too many").exists()


@pytest.mark.django_db()
def test_post_all_matching_session_mode_is_not_capped(client_with_user, team_with_users):
    """Session mode ships the filter rather than the ids, so the message-mode cap does not apply."""
    matching_exp = ExperimentFactory.create(team=team_with_users)
    ExperimentSessionFactory.create_batch(2, team=team_with_users, experiment=matching_exp)

    url = f"{_dataset_new_url(team_with_users)}?f_experiment={matching_exp.id}&op_experiment=any+of"
    with (
        patch("apps.evaluations.dataset_clone.MESSAGE_MODE_ALL_MATCHING_LIMIT", 1),
        patch("apps.evaluations.forms.create_dataset_from_sessions_task.delay") as mock_delay,
    ):
        mock_delay.return_value.id = "task-id"
        response = client_with_user.post(
            url,
            {
                "name": "Many sessions",
                "evaluation_mode": "session",
                "mode": "clone",
                "session_ids": "",
                "session_scope": "all_matching",
            },
        )

    assert response.status_code == 302, response.context["form"].errors
    mock_delay.assert_called_once()
