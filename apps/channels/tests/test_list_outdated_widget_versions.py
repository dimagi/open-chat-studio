from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.channels import widget_versions
from apps.channels.models import ChannelPlatform
from apps.channels.widget_versions import LATEST_VERSION, UNKNOWN_WIDGET_VERSION, WidgetDeprecation
from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory


def _widget_channel(version, platform=ChannelPlatform.EMBEDDED_WIDGET, **kwargs):
    kwargs.setdefault("widget_version_updated_at", timezone.now() if version else None)
    return ExperimentChannelFactory.create(
        platform=platform,
        widget_version=version,
        extra_data={},
        **kwargs,
    )


def _add_session(channel, human_messages=1, ai_messages=0, days_ago=0):
    session = ExperimentSessionFactory.create(
        team=channel.team, experiment=channel.experiment, experiment_channel=channel
    )
    for message_type, count in ((ChatMessageType.HUMAN, human_messages), (ChatMessageType.AI, ai_messages)):
        for _ in range(count):
            message = ChatMessage.objects.create(chat=session.chat, message_type=message_type, content="hi")
            if days_ago:
                ChatMessage.objects.filter(id=message.id).update(created_at=timezone.now() - timedelta(days=days_ago))
    return session


def _run(**options):
    out = StringIO()
    call_command("list_outdated_widget_versions", stdout=out, **options)
    return out.getvalue()


@pytest.mark.django_db()
def test_lists_outdated_channel_with_session_count():
    channel = _widget_channel("0.7.0")
    _add_session(channel, human_messages=3)
    _add_session(channel, human_messages=1)

    output = _run()

    assert channel.experiment.name in output
    assert "0.7.0" in output
    assert "outdated" in output
    # two sessions, not four messages
    assert "Sessions: 2" in output


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("version", "expected_status"),
    [
        pytest.param("0.5.0", "deprecated", id="below-deprecation-cutoff"),
        pytest.param(UNKNOWN_WIDGET_VERSION, "deprecated", id="pre-header-widget"),
        pytest.param("not-a-version", "deprecated", id="unparseable"),
        pytest.param("0.7.0", "outdated", id="outdated-but-supported"),
    ],
)
def test_status_classification(version, expected_status):
    channel = _widget_channel(version)
    _add_session(channel)
    # Pin the deprecation window relative to now; running against the real DEPRECATIONS
    # entry would flip every "deprecated" case to "sunset" once its date passes.
    pending = WidgetDeprecation(below_version="0.6.0", sunset_at=timezone.now() + timedelta(days=30))

    with patch("apps.channels.widget_versions.DEPRECATIONS", [pending]):
        output = _run()

    assert expected_status in output
    assert "Chatbots: 1" in output


@pytest.mark.django_db()
def test_sunset_status_for_elapsed_deprecation():
    channel = _widget_channel("0.5.0")
    _add_session(channel)
    elapsed = WidgetDeprecation(below_version="0.6.0", sunset_at=timezone.now() - timedelta(days=1))

    with patch("apps.channels.widget_versions.DEPRECATIONS", [elapsed]):
        output = _run()

    assert channel.experiment.name in output
    assert "sunset" in output


@pytest.mark.django_db()
def test_unparseable_version_still_reported_without_deprecations():
    """A pre-header widget is older than every release, so it is reported even with no
    deprecation configured to catch it."""
    channel = _widget_channel(UNKNOWN_WIDGET_VERSION)
    _add_session(channel)

    with patch("apps.channels.widget_versions.DEPRECATIONS", []):
        output = _run()

    assert channel.experiment.name in output
    assert "outdated" in output


@pytest.mark.django_db()
def test_current_version_excluded():
    channel = _widget_channel(LATEST_VERSION)
    _add_session(channel)

    assert channel.experiment.name not in _run()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        pytest.param({"human_messages": 0, "ai_messages": 2}, "no human messages", id="ai-only-session"),
        pytest.param({"days_ago": 45}, "outside window", id="stale-session"),
    ],
)
def test_channels_without_qualifying_usage_excluded(kwargs, reason):
    channel = _widget_channel("0.7.0")
    _add_session(channel, **kwargs)

    assert channel.experiment.name not in _run(), reason


@pytest.mark.django_db()
def test_window_is_configurable():
    channel = _widget_channel("0.7.0")
    _add_session(channel, days_ago=45)

    assert channel.experiment.name not in _run()
    assert channel.experiment.name in _run(days=60)


@pytest.mark.django_db()
def test_non_widget_platform_excluded():
    channel = _widget_channel("0.7.0", platform=ChannelPlatform.WEB)
    _add_session(channel)

    assert channel.experiment.name not in _run()


@pytest.mark.django_db()
def test_unreported_version_requires_opt_in():
    channel = _widget_channel(None)
    _add_session(channel)

    assert channel.experiment.name not in _run()

    output = _run(include_unreported=True)
    assert channel.experiment.name in output
    assert "not reported" in output
    assert "Unreported: 1" in output


@pytest.mark.django_db()
def test_unreported_version_gets_no_deprecation_verdict():
    """A null version means the recording path never ran, not that the widget is old, so it
    must not be handed a deprecation status or sunset date."""
    channel = _widget_channel(None)
    _add_session(channel)

    output = _run(include_unreported=True)

    assert "unreported" in output
    assert "deprecated" not in output
    assert "sunset" not in output
    # no fabricated sunset date, and excluded from the deprecation-only view
    assert f"{widget_versions.latest_deprecation().sunset_at:%Y-%m-%d}" not in output
    assert channel.experiment.name not in _run(include_unreported=True, deprecated_only=True)


@pytest.mark.django_db()
def test_deprecated_only_filters_merely_outdated():
    outdated = _widget_channel("0.7.0")
    deprecated = _widget_channel("0.5.0")
    _add_session(outdated)
    _add_session(deprecated)

    output = _run(deprecated_only=True)

    assert deprecated.experiment.name in output
    assert outdated.experiment.name not in output


@pytest.mark.django_db()
def test_team_filter():
    included = _widget_channel("0.7.0")
    excluded = _widget_channel("0.7.0")
    _add_session(included)
    _add_session(excluded)

    output = _run(team=included.team.slug)

    assert included.experiment.name in output
    assert excluded.experiment.name not in output


@pytest.mark.django_db()
def test_csv_output():
    channel = _widget_channel("0.7.0")
    _add_session(channel, human_messages=2)

    output = _run(format="csv")

    lines = output.strip().splitlines()
    assert lines[0].startswith("Team,Team Slug,Chatbot")
    assert len(lines) == 2
    assert channel.team.slug in lines[1]
    assert lines[1].endswith(",1")


@pytest.mark.django_db()
def test_no_results_message():
    assert "No active chatbots are running an outdated widget version." in _run()


@pytest.mark.django_db()
def test_public_link_channels_are_left_out():
    """A public link serves the widget bundled with the platform, so its version moves with
    the deploy rather than with anything the team can upgrade."""
    channel = _widget_channel("0.7.0", platform=ChannelPlatform.PUBLIC)
    _add_session(channel)

    output = _run()

    assert channel.experiment.name not in output
    assert "No active chatbots are running an outdated widget version." in output
