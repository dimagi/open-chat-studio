from datetime import UTC, datetime, timedelta

import pytest
from django.test import RequestFactory
from django.utils import timezone

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.const import LEGACY_WEB_CHAT_SUCCESSOR_URL, LEGACY_WEB_CHAT_SUNSET_AT
from apps.experiments.models import ExperimentSession
from apps.experiments.sunsets import (
    LEGACY_WEB_CHAT,
    affected_experiments,
    legacy_web_chat_gone,
    live_public_channel,
)
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.sunset import SUNSETS

TOKEN = "public_token_1234567890123456789012"


def _web_session(experiment, *, days_ago=1, **kwargs):
    channel = ExperimentChannel.objects.get_team_web_channel(experiment.team)
    session = ExperimentSessionFactory.create(experiment=experiment, experiment_channel=channel, **kwargs)
    ExperimentSession.objects.filter(pk=session.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    return session


def _public_channel(experiment, *, enabled=True, token=TOKEN):
    return ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.PUBLIC,
        enabled=enabled,
        extra_data={"widget_token": token} if token else {},
    )


def test_spec_is_registered():
    assert SUNSETS["legacy_web_chat"] is LEGACY_WEB_CHAT
    assert LEGACY_WEB_CHAT.sunset_at == LEGACY_WEB_CHAT_SUNSET_AT
    assert LEGACY_WEB_CHAT.successor_url == LEGACY_WEB_CHAT_SUCCESSOR_URL
    assert LEGACY_WEB_CHAT.enforce_flag == "flag_sunset_legacy_web_chat_enforce"


def test_sunset_date_is_timezone_aware_utc():
    assert LEGACY_WEB_CHAT_SUNSET_AT.tzinfo is UTC
    assert isinstance(LEGACY_WEB_CHAT_SUNSET_AT, datetime)


@pytest.mark.django_db()
def test_affected_includes_a_chatbot_with_a_recent_web_session():
    experiment = ExperimentFactory.create()
    _web_session(experiment)
    assert list(affected_experiments()) == [experiment]


@pytest.mark.django_db()
def test_affected_includes_an_unaccepted_invitation():
    experiment = ExperimentFactory.create()
    _web_session(experiment, status="setup")
    assert list(affected_experiments()) == [experiment]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(lambda e: _web_session(e, days_ago=31), id="web-session-older-than-30-days"),
        pytest.param(lambda e: ExperimentSessionFactory.create(experiment=e), id="session-on-another-platform"),
        pytest.param(lambda e: None, id="no-sessions"),
    ],
)
def test_affected_excludes_chatbots_without_recent_web_sessions(setup):
    experiment = ExperimentFactory.create()
    setup(experiment)
    assert list(affected_experiments()) == []


@pytest.mark.django_db()
def test_affected_excludes_a_chatbot_with_a_live_public_link():
    experiment = ExperimentFactory.create()
    experiment.create_new_version(make_default=True)
    _public_channel(experiment)
    _web_session(experiment)
    assert list(affected_experiments()) == []


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("publish", "channel_kwargs"),
    [
        pytest.param(False, {}, id="nothing-published"),
        pytest.param(True, {"enabled": False}, id="channel-disabled"),
        pytest.param(True, {"token": ""}, id="no-token"),
    ],
)
def test_affected_keeps_a_chatbot_whose_public_link_is_not_live(publish, channel_kwargs):
    experiment = ExperimentFactory.create()
    if publish:
        experiment.create_new_version(make_default=True)
    _public_channel(experiment, **channel_kwargs)
    _web_session(experiment)
    assert list(affected_experiments()) == [experiment]


@pytest.mark.django_db()
def test_affected_excludes_archived_chatbots():
    experiment = ExperimentFactory.create()
    _web_session(experiment)
    experiment.is_archived = True
    experiment.save()
    assert list(affected_experiments()) == []


@pytest.mark.django_db()
def test_affected_returns_working_versions_only():
    experiment = ExperimentFactory.create()
    _web_session(experiment)
    experiment.create_new_version(make_default=True)
    assert list(affected_experiments()) == [experiment]


@pytest.mark.django_db()
def test_live_public_channel_returns_the_channel():
    experiment = ExperimentFactory.create()
    experiment.create_new_version(make_default=True)
    channel = _public_channel(experiment)
    assert live_public_channel(experiment) == channel


@pytest.mark.django_db()
def test_live_public_channel_requires_a_published_version():
    experiment = ExperimentFactory.create()
    _public_channel(experiment)
    assert live_public_channel(experiment) is None


@pytest.mark.django_db()
def test_live_public_channel_requires_an_enabled_channel():
    experiment = ExperimentFactory.create()
    experiment.create_new_version(make_default=True)
    _public_channel(experiment, enabled=False)
    assert live_public_channel(experiment) is None


@pytest.mark.django_db()
def test_live_public_channel_requires_a_token():
    experiment = ExperimentFactory.create()
    experiment.create_new_version(make_default=True)
    _public_channel(experiment, token="")
    assert live_public_channel(experiment) is None


def _gone(experiment, team=None):
    request = RequestFactory().get("/")
    request.team = team or experiment.team
    return legacy_web_chat_gone(request, request.team.slug, experiment.public_id)


@pytest.mark.django_db()
def test_gone_names_the_public_link_when_one_is_live():
    experiment = ExperimentFactory.create()
    experiment.create_new_version(make_default=True)
    channel = _public_channel(experiment)
    response = _gone(experiment)
    assert response.status_code == 410
    assert channel.public_url in response.content.decode()


@pytest.mark.django_db()
def test_gone_names_the_docs_when_there_is_no_public_link():
    experiment = ExperimentFactory.create()
    body = _gone(experiment).content.decode()
    assert LEGACY_WEB_CHAT_SUCCESSOR_URL in body
    assert "/c/" not in body


@pytest.mark.django_db()
def test_gone_does_not_name_another_teams_public_link():
    experiment = ExperimentFactory.create()
    experiment.create_new_version(make_default=True)
    _public_channel(experiment)
    body = _gone(experiment, team=TeamFactory.create()).content.decode()
    assert "/c/" not in body


def test_message_names_the_date_and_the_count():
    message = LEGACY_WEB_CHAT.build_message([object(), object()], "T-14")
    assert "2 chatbot(s)" in message
    assert f"{LEGACY_WEB_CHAT_SUNSET_AT:%d %b %Y}" in message
