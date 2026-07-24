import pytest

from apps.oauth.forms import RegisterApplicationForm
from apps.oauth.models import OAuth2Application
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def user_with_team(db):
    team = TeamWithUsersFactory.create()
    return team.members.first(), team


def _form_data(**overrides):
    data = {
        "name": "My App",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "algorithm": "RS256",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db()
def test_client_credentials_requires_team_not_redirect_uris(user_with_team):
    user, team = user_with_team
    form = RegisterApplicationForm(
        data=_form_data(authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS, team=team.id),
        user=user,
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db()
def test_client_credentials_without_team_is_invalid(user_with_team):
    user, _team = user_with_team
    form = RegisterApplicationForm(
        data=_form_data(authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS),
        user=user,
    )
    assert not form.is_valid()
    assert "team" in form.errors


@pytest.mark.django_db()
def test_authorization_code_requires_redirect_uris_not_team(user_with_team):
    user, _team = user_with_team
    form = RegisterApplicationForm(
        data=_form_data(
            authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris="https://example.com/callback",
        ),
        user=user,
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db()
def test_authorization_code_without_redirect_uris_is_invalid(user_with_team):
    user, _team = user_with_team
    form = RegisterApplicationForm(
        data=_form_data(authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE),
        user=user,
    )
    assert not form.is_valid()
    assert "redirect_uris" in form.errors


@pytest.mark.django_db()
def test_team_and_grant_type_immutable_after_creation(user_with_team):
    user, team = user_with_team
    app = OAuth2Application.objects.create(
        name="machine-app",
        user=user,
        team=team,
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
    )
    form = RegisterApplicationForm(instance=app, user=user)
    assert form.fields["team"].disabled
    assert form.fields["authorization_grant_type"].disabled


@pytest.mark.django_db()
def test_team_choices_scoped_to_user(user_with_team):
    user, team = user_with_team
    other_team = TeamWithUsersFactory.create()
    form = RegisterApplicationForm(user=user)
    team_ids = set(form.fields["team"].queryset.values_list("id", flat=True))
    assert team.id in team_ids
    assert other_team.id not in team_ids
