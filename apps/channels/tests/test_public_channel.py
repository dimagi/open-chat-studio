"""Regenerating a public link revokes it (spec D2): the old token stops new starts at once,
and every live session on the channel is ended so a token-required session cannot keep running
for the rest of its token lifetime."""

from unittest.mock import Mock

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.channels.forms import PublicChannelForm
from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.teams.models import Flag
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory

TOKEN = "public_token_1234567890123456789012"


@pytest.fixture()
def public_channel(experiment):
    return ExperimentChannelFactory.create(
        team=experiment.team, experiment=experiment, platform=ChannelPlatform.PUBLIC, extra_data={"widget_token": TOKEN}
    )


@pytest.mark.django_db()
def test_public_url_is_the_absolute_token_route(public_channel):
    assert public_channel.public_url.endswith(reverse("public_link", args=[TOKEN]))
    assert public_channel.public_url.startswith("http")


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "status",
    [
        pytest.param(SessionStatus.ACTIVE, id="active"),
        pytest.param(SessionStatus.SETUP, id="setup"),
        pytest.param(SessionStatus.PENDING, id="pending"),
    ],
)
def test_end_live_sessions_ends_each_live_status(public_channel, status):
    session = ExperimentSessionFactory.create(
        experiment=public_channel.experiment, experiment_channel=public_channel, status=status
    )
    other = ExperimentSessionFactory.create(experiment=public_channel.experiment, status=SessionStatus.ACTIVE)

    public_channel.end_live_sessions()

    session.refresh_from_db()
    other.refresh_from_db()
    assert session.is_complete is True
    assert session.ended_at is not None
    assert other.status == SessionStatus.ACTIVE


@pytest.mark.django_db()
def test_end_live_sessions_reports_the_count_and_skips_complete_ones(public_channel):
    ExperimentSessionFactory.create_batch(
        2, experiment=public_channel.experiment, experiment_channel=public_channel, status=SessionStatus.ACTIVE
    )
    ExperimentSessionFactory.create(
        experiment=public_channel.experiment, experiment_channel=public_channel, status=SessionStatus.COMPLETE
    )
    assert public_channel.end_live_sessions() == 2
    complete_count = ExperimentSession.objects.filter(
        experiment_channel=public_channel, status=SessionStatus.COMPLETE
    ).count()
    assert complete_count == 3


class TestPublicChannelForm:
    def test_new_channel_gets_a_token_and_lists(self):
        form = PublicChannelForm(
            data={"welcome_messages": "Hello\nHow can I help?", "starter_questions": "Opening hours"},
            experiment=Mock(),
        )
        assert form.is_valid(), form.errors
        assert len(form.cleaned_data["widget_token"]) == 32
        assert form.cleaned_data["welcome_messages"] == ["Hello", "How can I help?"]
        assert form.cleaned_data["starter_questions"] == ["Opening hours"]

    def test_lists_are_optional(self):
        form = PublicChannelForm(data={}, experiment=Mock())
        assert form.is_valid(), form.errors
        assert form.cleaned_data["welcome_messages"] == []
        assert form.cleaned_data["starter_questions"] == []

    def test_existing_token_is_preserved(self):
        channel = Mock()
        channel.extra_data = {"widget_token": TOKEN, "welcome_messages": [], "starter_questions": []}
        form = PublicChannelForm(data={}, channel=channel, experiment=Mock())
        assert form.is_valid(), form.errors
        assert form.cleaned_data["widget_token"] == TOKEN

    def test_regenerate_mints_a_new_token(self):
        channel = Mock()
        channel.extra_data = {"widget_token": TOKEN}
        form = PublicChannelForm(data={"regenerate_link": "1"}, channel=channel, experiment=Mock())
        assert form.is_valid(), form.errors
        assert form.cleaned_data["widget_token"] != TOKEN
        assert len(form.cleaned_data["widget_token"]) == 32


@pytest.mark.django_db()
def test_saving_a_regenerated_form_ends_live_sessions(public_channel):
    live = ExperimentSessionFactory.create(
        experiment=public_channel.experiment, experiment_channel=public_channel, status=SessionStatus.ACTIVE
    )
    form = PublicChannelForm(
        data={"regenerate_link": "1"}, channel=public_channel, experiment=public_channel.experiment
    )
    assert form.is_valid(), form.errors
    public_channel.extra_data = form.cleaned_data
    public_channel.save()
    form.post_save(public_channel)
    live.refresh_from_db()
    assert live.is_complete
    assert "ended" in form.success_message


def _operator(client, team, codename):
    user = team.members.first()
    user.user_permissions.add(Permission.objects.get(codename=codename))
    client.force_login(user)
    return user


@pytest.fixture()
def public_flag(experiment):
    flag = Flag.objects.create(name="flag_public_channel")
    flag.teams.add(experiment.team)
    flag.flush()
    return flag


@pytest.mark.django_db()
def test_create_dialog_makes_a_public_channel_with_a_token(client, experiment, public_flag):
    _operator(client, experiment.team, "add_experimentchannel")
    url = reverse("channels:channel_create_dialog", args=[experiment.team.slug, experiment.id, "public"])
    response = client.post(
        url,
        data={
            "name": "Public link",
            "platform": "public",
            "enabled": "on",
            "welcome_messages": "Hello",
            "starter_questions": "",
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200, response.content
    channel = experiment.experimentchannel_set.get(platform=ChannelPlatform.PUBLIC)
    assert len(channel.extra_data["widget_token"]) == 32
    assert channel.extra_data["welcome_messages"] == ["Hello"]
    assert channel.public_url.encode() in response.content


@pytest.mark.django_db()
def test_create_dialog_refuses_without_the_flag(client, experiment):
    _operator(client, experiment.team, "add_experimentchannel")
    url = reverse("channels:channel_create_dialog", args=[experiment.team.slug, experiment.id, "public"])
    response = client.get(url)
    assert response.status_code == 302


@pytest.mark.django_db()
def test_edit_dialog_regenerates_and_ends_sessions(client, public_channel, public_flag):
    _operator(client, public_channel.team, "change_experimentchannel")
    live = ExperimentSessionFactory.create(
        experiment=public_channel.experiment, experiment_channel=public_channel, status=SessionStatus.ACTIVE
    )
    url = reverse(
        "channels:channel_edit_dialog",
        args=[public_channel.team.slug, public_channel.experiment_id, public_channel.id],
    )
    response = client.post(
        url,
        data={"name": public_channel.name, "platform": "public", "enabled": "on", "regenerate_link": "1"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200, response.content
    public_channel.refresh_from_db()
    live.refresh_from_db()
    assert public_channel.extra_data["widget_token"] != TOKEN
    assert live.is_complete
    assert b"Link regenerated" in response.content


def test_blank_lines_are_dropped_from_the_lists():
    form = PublicChannelForm(
        data={"welcome_messages": "Hello\r\n\r\nWorld\r\n", "starter_questions": "  \r\nHours?\r\n"},
        experiment=Mock(),
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["welcome_messages"] == ["Hello", "World"]
    assert form.cleaned_data["starter_questions"] == ["Hours?"]


@pytest.mark.django_db()
def test_disabling_through_the_dialog_ends_live_sessions(client, public_channel, public_flag):
    _operator(client, public_channel.team, "change_experimentchannel")
    live = ExperimentSessionFactory.create(
        experiment=public_channel.experiment, experiment_channel=public_channel, status=SessionStatus.ACTIVE
    )
    url = reverse(
        "channels:channel_edit_dialog",
        args=[public_channel.team.slug, public_channel.experiment_id, public_channel.id],
    )
    response = client.post(
        url,
        data={"name": public_channel.name, "platform": "public", "disabled_message": "Back soon"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200, response.content
    public_channel.refresh_from_db()
    live.refresh_from_db()
    assert public_channel.is_disabled
    assert public_channel.extra_data["widget_token"] == TOKEN
    assert live.is_complete


@pytest.mark.django_db()
def test_removing_a_public_channel_ends_live_sessions(client, public_channel):
    _operator(client, public_channel.team, "delete_experimentchannel")
    live = ExperimentSessionFactory.create(
        experiment=public_channel.experiment, experiment_channel=public_channel, status=SessionStatus.ACTIVE
    )
    url = reverse(
        "channels:delete_channel", args=[public_channel.team.slug, public_channel.experiment_id, public_channel.id]
    )
    assert client.post(url).status_code == 200
    live.refresh_from_db()
    assert live.is_complete
