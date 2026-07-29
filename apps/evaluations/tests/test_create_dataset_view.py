import pytest
from django.test import Client
from django.urls import reverse

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
def test_get_prepopulates_session_ids_from_new_format_filters(client_with_user, team_with_users):
    """Regression: arriving at "Create dataset" with new-format (f_/op_) filters must pre-select
    the filtered sessions. The stale ``filter_`` prefix check silently disabled this for the new
    URL format, so no sessions were pre-populated."""
    matching_exp = ExperimentFactory.create(team=team_with_users)
    other_exp = ExperimentFactory.create(team=team_with_users)
    matching = ExperimentSessionFactory.create(team=team_with_users, experiment=matching_exp)
    other = ExperimentSessionFactory.create(team=team_with_users, experiment=other_exp)

    response = client_with_user.get(
        _dataset_new_url(team_with_users),
        {"f_experiment": str(matching_exp.id), "op_experiment": "any of"},
    )

    assert response.status_code == 200
    session_ids = response.context["form"].initial.get("session_ids", "")
    assert str(matching.external_id) in session_ids
    assert str(other.external_id) not in session_ids


@pytest.mark.django_db()
def test_get_without_filters_does_not_prepopulate_sessions(client_with_user, team_with_users):
    """Without explicit filter params in the URL, no sessions should be pre-selected."""
    ExperimentSessionFactory.create(team=team_with_users)

    response = client_with_user.get(_dataset_new_url(team_with_users))

    assert response.status_code == 200
    assert not response.context["form"].initial.get("session_ids")
