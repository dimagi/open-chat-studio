"""Regenerating a public link revokes it (spec D2): the old token stops new starts at once,
and every live session on the channel is ended so a token-required session cannot keep running
for the rest of its token lifetime."""

import pytest
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession, SessionStatus
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
def test_regenerate_replaces_the_token(public_channel):
    new_token = public_channel.regenerate_widget_token()
    public_channel.refresh_from_db()
    assert new_token != TOKEN
    assert len(new_token) == 32
    assert public_channel.extra_data["widget_token"] == new_token


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "status",
    [
        pytest.param(SessionStatus.ACTIVE, id="active"),
        pytest.param(SessionStatus.SETUP, id="setup"),
        pytest.param(SessionStatus.PENDING, id="pending"),
    ],
)
def test_regenerate_ends_live_sessions(public_channel, status):
    session = ExperimentSessionFactory.create(
        experiment=public_channel.experiment, experiment_channel=public_channel, status=status
    )
    other = ExperimentSessionFactory.create(experiment=public_channel.experiment, status=SessionStatus.ACTIVE)

    public_channel.regenerate_widget_token()

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
