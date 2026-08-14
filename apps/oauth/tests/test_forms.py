import pytest

from apps.experiments.models import Experiment
from apps.oauth.forms import AuthorizationForm, RegisterApplicationForm, RegisterGlobalApplicationForm
from apps.oauth.models import OAuth2Application
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import MembershipFactory, TeamWithUsersFactory


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
def test_client_credentials_does_not_require_redirect_uris():
    form = RegisterApplicationForm(
        data=_form_data(authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS),
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db()
def test_authorization_code_requires_redirect_uris():
    form = RegisterApplicationForm(
        data=_form_data(authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE),
    )
    assert not form.is_valid()
    assert "redirect_uris" in form.errors


@pytest.mark.django_db()
def test_authorization_code_valid_with_redirect_uris():
    form = RegisterApplicationForm(
        data=_form_data(
            authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris="https://example.com/callback",
        ),
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db()
def test_team_is_not_a_form_field():
    """The team comes from the URL, never from the payload, so it cannot be set or changed here."""
    assert "team" not in RegisterApplicationForm().fields
    assert "team" not in RegisterGlobalApplicationForm().fields


@pytest.mark.django_db()
def test_grant_type_immutable_after_creation(user_with_team):
    user, team = user_with_team
    app = OAuth2Application.objects.create(
        name="machine-app",
        user=user,
        team=team,
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
    )
    form = RegisterApplicationForm(instance=app)
    assert form.fields["authorization_grant_type"].disabled


@pytest.mark.django_db()
def test_global_form_ignores_client_credentials():
    """A global client-credentials application would issue tokens scoped to no team at all."""
    form = RegisterGlobalApplicationForm(
        data=_form_data(authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS),
    )
    assert not form.is_valid()
    # The grant type field is disabled, so the posted value is ignored in favour of authorization-code,
    # which requires redirect URIs.
    assert "redirect_uris" in form.errors
    assert form.cleaned_data["authorization_grant_type"] == OAuth2Application.GRANT_AUTHORIZATION_CODE


@pytest.mark.django_db()
def test_global_form_does_not_rewrite_the_grant_type_of_an_existing_application():
    """Editing must not silently re-scope an application's tokens by changing its grant type."""
    app = OAuth2Application.objects.create(
        name="machine-app",
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
    )
    form = RegisterGlobalApplicationForm(instance=app, data=_form_data(name="renamed"))

    assert form.initial["authorization_grant_type"] == OAuth2Application.GRANT_CLIENT_CREDENTIALS
    assert not form.is_valid()
    assert "authorization_grant_type" in form.errors


@pytest.mark.django_db()
def test_global_form_saves_as_authorization_code():
    form = RegisterGlobalApplicationForm(data=_form_data(redirect_uris="https://example.com/callback"))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["authorization_grant_type"] == OAuth2Application.GRANT_AUTHORIZATION_CODE


def _client_credentials_app(team, name="machine-app"):
    return OAuth2Application.objects.create(
        name=name,
        team=team,
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
    )


@pytest.mark.django_db()
def test_allowed_chatbots_offers_only_the_teams_working_versions(user_with_team):
    _user, team = user_with_team
    mine = ExperimentFactory.create(team=team)
    version = mine.create_new_version()
    archived = ExperimentFactory.create(team=team, is_archived=True)
    other_team = ExperimentFactory.create(team=TeamWithUsersFactory.create())

    offered = set(RegisterApplicationForm(team=team).fields["allowed_chatbots"].queryset)

    assert offered == {mine}
    assert version not in offered
    assert archived not in offered
    assert other_team not in offered


@pytest.mark.django_db()
def test_allowed_chatbots_saved_for_client_credentials(user_with_team):
    _user, team = user_with_team
    chatbot = ExperimentFactory.create(team=team)
    form = RegisterApplicationForm(
        team=team,
        data=_form_data(
            authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
            allowed_chatbots=[chatbot.pk],
        ),
    )

    assert form.is_valid(), form.errors
    form.instance.team = team
    app = form.save()
    assert list(app.allowed_chatbots.all()) == [chatbot]


@pytest.mark.django_db()
def test_allowed_chatbots_cleared_for_authorization_code(user_with_team):
    """An authorization-code token carries a user, so it keeps team-membership semantics."""
    _user, team = user_with_team
    chatbot = ExperimentFactory.create(team=team)
    form = RegisterApplicationForm(
        team=team,
        data=_form_data(
            authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris="https://example.com/callback",
            allowed_chatbots=[chatbot.pk],
        ),
    )

    assert form.is_valid(), form.errors
    form.instance.team = team
    app = form.save()
    assert list(app.allowed_chatbots.all()) == []


@pytest.mark.django_db()
def test_archived_chatbot_survives_an_unrelated_save(user_with_team):
    """A ModelMultipleChoiceField drops selections outside its queryset -- archived ones included."""
    _user, team = user_with_team
    archived = ExperimentFactory.create(team=team, is_archived=True)
    app = _client_credentials_app(team)
    app.allowed_chatbots.add(archived)

    form = RegisterApplicationForm(instance=app, team=team)
    assert form.initial["allowed_chatbots"] == [archived.pk]

    form = RegisterApplicationForm(
        instance=app,
        team=team,
        data=_form_data(
            name="renamed",
            authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
            allowed_chatbots=[archived.pk],
        ),
    )
    assert form.is_valid(), form.errors
    form.save()

    # Read through get_all(): the related manager filters archived rows out of its own queryset.
    assert list(Experiment.objects.get_all().filter(oauth_applications=app)) == [archived]


@pytest.mark.django_db()
def test_global_form_omits_allowed_chatbots():
    """A global application has no team whose chatbots could be offered."""
    assert "allowed_chatbots" not in RegisterGlobalApplicationForm().fields


def _authorization_form_data(team_slug=None):
    data = {
        "redirect_uri": "https://example.com/callback",
        "client_id": "test-client-id",
        "response_type": "code",
        "allow": True,
    }
    if team_slug:
        data["team_slug"] = team_slug
    return data


@pytest.mark.django_db()
def test_authorization_form_pins_team_of_team_scoped_application(user_with_team):
    """The team of a team-scoped application is shown but cannot be swapped for another."""
    user, team = user_with_team
    other_team = TeamWithUsersFactory.create()
    MembershipFactory.create(team=other_team, user=user)

    form = AuthorizationForm(
        user,
        team,
        False,
        data=_authorization_form_data(team_slug=other_team.slug),
        initial={"team_slug": team.slug},
    )

    assert form.fields["team_slug"].disabled
    assert form.fields["team_slug"].choices == [(team.slug, team.name)]
    assert form.is_valid(), form.errors
    assert form.cleaned_data["team_slug"] == team.slug


@pytest.mark.django_db()
def test_authorization_form_offers_all_teams_for_global_application(user_with_team):
    user, team = user_with_team
    other_team = TeamWithUsersFactory.create()
    MembershipFactory.create(team=other_team, user=user)

    form = AuthorizationForm(user, None, False, data=_authorization_form_data(team_slug=other_team.slug))

    assert not form.fields["team_slug"].disabled
    assert {slug for slug, _label in form.fields["team_slug"].choices} == {team.slug, other_team.slug}
    assert form.is_valid(), form.errors
    assert form.cleaned_data["team_slug"] == other_team.slug


@pytest.mark.django_db()
def test_authorization_form_rejects_team_user_is_not_a_member_of(user_with_team):
    user, _team = user_with_team
    other_team = TeamWithUsersFactory.create()

    # Supplied as initial data (as it would be for a disabled field) so that it gets past the choice
    # validation and reaches the membership check.
    form = AuthorizationForm(
        user,
        other_team,
        False,
        data=_authorization_form_data(),
        initial={"team_slug": other_team.slug},
    )

    assert not form.is_valid()
    assert "team_slug" in form.errors
