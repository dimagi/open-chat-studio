import pytest

from apps.channels.models import ChannelPlatform
from apps.experiments.models import Participant
from apps.participants.forms import ParticipantForm
from apps.utils.factories.experiment import ParticipantFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_participant_form_creates_participant():
    team = TeamFactory.create()
    form = ParticipantForm(
        data={"identifier": "user@example.com", "platform": ChannelPlatform.WEB, "name": "Alice"},
        team=team,
    )
    assert form.is_valid(), form.errors
    participant = form.save(commit=False)
    participant.team = team
    participant.save()
    assert participant.identifier == "user@example.com"
    assert participant.platform == ChannelPlatform.WEB
    assert participant.name == "Alice"


@pytest.mark.django_db()
def test_participant_form_name_is_optional():
    team = TeamFactory.create()
    form = ParticipantForm(
        data={"identifier": "user@example.com", "platform": ChannelPlatform.WEB},
        team=team,
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db()
def test_participant_form_requires_identifier_and_platform():
    team = TeamFactory.create()
    form = ParticipantForm(data={}, team=team)
    assert not form.is_valid()
    assert "identifier" in form.errors
    assert "platform" in form.errors


@pytest.mark.django_db()
def test_participant_form_platform_choices_include_web_and_api():
    team = TeamFactory.create()
    form = ParticipantForm(team=team)
    values = [value for value, _label in form.fields["platform"].choices]
    assert ChannelPlatform.WEB in values
    assert ChannelPlatform.API in values


@pytest.mark.django_db()
def test_participant_form_rejects_duplicate_with_link():
    team = TeamFactory.create()
    existing = ParticipantFactory.create(team=team, platform=ChannelPlatform.WEB, identifier="user@example.com")
    form = ParticipantForm(
        data={"identifier": "user@example.com", "platform": ChannelPlatform.WEB},
        team=team,
    )
    assert not form.is_valid()
    error_html = str(form.non_field_errors())
    assert existing.get_absolute_url() in error_html


@pytest.mark.django_db()
def test_participant_form_allows_same_identifier_on_different_platform():
    team = TeamFactory.create()
    ParticipantFactory.create(team=team, platform=ChannelPlatform.WEB, identifier="user@example.com")
    form = ParticipantForm(
        data={"identifier": "user@example.com", "platform": ChannelPlatform.TELEGRAM},
        team=team,
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db()
def test_participant_form_allows_same_identifier_on_different_team():
    team_a = TeamFactory.create()
    team_b = TeamFactory.create()
    ParticipantFactory.create(team=team_a, platform=ChannelPlatform.WEB, identifier="user@example.com")
    form = ParticipantForm(
        data={"identifier": "user@example.com", "platform": ChannelPlatform.WEB},
        team=team_b,
    )
    assert form.is_valid(), form.errors
    # Sanity: no row exists yet for team_b
    assert not Participant.objects.filter(team=team_b).exists()
