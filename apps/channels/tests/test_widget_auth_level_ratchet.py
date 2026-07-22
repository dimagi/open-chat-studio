from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from field_audit import enable_audit
from field_audit.models import AuditEvent

from apps.channels.forms import WidgetParams
from apps.channels.models import ChannelPlatform, ExperimentChannel, WidgetAuthLevel
from apps.channels.tasks import ratchet_widget_auth_levels
from apps.channels.widget_versions import (
    AUTH_LEVEL_EMBED_KEY,
    AUTH_LEVEL_NONE,
    AUTH_LEVEL_SESSION_TOKEN,
    level_for_version,
    min_version_for_level,
)
from apps.ocs_notifications.notifications import widget_auth_level_upgrade_notification
from apps.utils.factories.channels import ExperimentChannelFactory


@pytest.mark.parametrize(
    ("widget_version", "expected"),
    [
        pytest.param("unknown", AUTH_LEVEL_NONE, id="unknown-placeholder"),
        pytest.param("0.4.8", AUTH_LEVEL_NONE, id="pre-0.5.1"),
        pytest.param("0.5.0", AUTH_LEVEL_NONE, id="just-below-embed-key"),
        pytest.param("0.5.1", AUTH_LEVEL_EMBED_KEY, id="embed-key-floor"),
        pytest.param("0.8.9", AUTH_LEVEL_EMBED_KEY, id="embed-key-ceiling"),
        pytest.param("0.9.0", AUTH_LEVEL_SESSION_TOKEN, id="session-token-floor"),
        pytest.param("0.10.0", AUTH_LEVEL_SESSION_TOKEN, id="session-token-above"),
        pytest.param("garbage", AUTH_LEVEL_NONE, id="unparseable"),
        pytest.param(None, AUTH_LEVEL_SESSION_TOKEN, id="never-connected"),
    ],
)
def test_level_for_version(widget_version, expected):
    assert level_for_version(widget_version) == expected


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        pytest.param(AUTH_LEVEL_NONE, None, id="none-has-no-floor"),
        pytest.param(AUTH_LEVEL_EMBED_KEY, "0.5.1", id="embed-key"),
        pytest.param(AUTH_LEVEL_SESSION_TOKEN, "0.9.0", id="session-token"),
    ],
)
def test_min_version_for_level(level, expected):
    assert min_version_for_level(level) == expected


def _widget_channel(**kwargs):
    return ExperimentChannelFactory(
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": "tok"},
        **kwargs,
    )


@pytest.mark.django_db()
def test_first_run_notifies_but_does_not_bump():
    channel = _widget_channel(
        required_auth_level=WidgetAuthLevel.EMBED_KEY,
        widget_version="0.9.1",
    )
    with patch("apps.channels.tasks.widget_auth_level_upgrade_notification") as notify:
        ratchet_widget_auth_levels()

    channel.refresh_from_db()
    assert channel.required_auth_level == WidgetAuthLevel.EMBED_KEY  # unchanged
    assert channel.pending_auth_level == WidgetAuthLevel.SESSION_TOKEN
    assert channel.auth_level_notified_at is not None
    notify.assert_called_once()
    assert notify.call_args.kwargs["min_version"] == "0.9.0"
    assert channel.experiment.name in notify.call_args.kwargs["affected_chatbots"]


@pytest.mark.django_db()
def test_second_run_before_grace_does_not_bump():
    channel = _widget_channel(
        required_auth_level=WidgetAuthLevel.EMBED_KEY,
        widget_version="0.9.1",
    )
    with patch("apps.channels.tasks.widget_auth_level_upgrade_notification"):
        ratchet_widget_auth_levels()
    channel.refresh_from_db()
    notified_at = channel.auth_level_notified_at

    # A day later, still within the grace window.
    channel.auth_level_notified_at = notified_at - timedelta(days=1)
    channel.save(update_fields=["auth_level_notified_at"])
    with patch("apps.channels.tasks.widget_auth_level_upgrade_notification") as notify:
        ratchet_widget_auth_levels()

    channel.refresh_from_db()
    assert channel.required_auth_level == WidgetAuthLevel.EMBED_KEY
    assert channel.pending_auth_level == WidgetAuthLevel.SESSION_TOKEN
    notify.assert_not_called()  # already notified


@pytest.mark.django_db()
def test_bump_applied_after_grace_and_audited():
    channel = _widget_channel(
        required_auth_level=WidgetAuthLevel.EMBED_KEY,
        widget_version="0.9.1",
        pending_auth_level=WidgetAuthLevel.SESSION_TOKEN,
    )
    # Notified longer ago than the grace period.
    channel.auth_level_notified_at = _past(ExperimentChannel.AUTH_LEVEL_RATCHET_GRACE + timedelta(days=1))
    channel.save(update_fields=["auth_level_notified_at"])

    # Auditing is disabled under test (FIELD_AUDIT_ENABLED = not IS_TESTING); opt in so the
    # level change the task makes via save() is recorded, as it would be in production.
    with enable_audit():
        ratchet_widget_auth_levels()

    channel.refresh_from_db()
    assert channel.required_auth_level == WidgetAuthLevel.SESSION_TOKEN
    assert channel.pending_auth_level is None
    assert channel.auth_level_notified_at is None

    audited = AuditEvent.objects.by_model(ExperimentChannel).filter(object_pk=channel.pk)
    assert any("required_auth_level" in (event.delta or {}) for event in audited)


@pytest.mark.django_db()
def test_never_downgrades():
    """A channel already at SESSION_TOKEN reporting an old version keeps its level."""
    channel = _widget_channel(
        required_auth_level=WidgetAuthLevel.SESSION_TOKEN,
        widget_version="0.6.0",
    )
    with patch("apps.channels.tasks.widget_auth_level_upgrade_notification") as notify:
        ratchet_widget_auth_levels()

    channel.refresh_from_db()
    assert channel.required_auth_level == WidgetAuthLevel.SESSION_TOKEN
    assert channel.pending_auth_level is None
    assert channel.auth_level_notified_at is None
    notify.assert_not_called()


@pytest.mark.django_db()
def test_pending_cleared_when_widget_downgrades_before_grace():
    """If the reported version drops below the pending level, the pending bump is dropped."""
    channel = _widget_channel(
        required_auth_level=WidgetAuthLevel.EMBED_KEY,
        widget_version="0.6.0",  # only satisfies EMBED_KEY now
        pending_auth_level=WidgetAuthLevel.SESSION_TOKEN,
    )
    channel.auth_level_notified_at = _past(timedelta(days=1))
    channel.save(update_fields=["auth_level_notified_at"])

    with patch("apps.channels.tasks.widget_auth_level_upgrade_notification") as notify:
        ratchet_widget_auth_levels()

    channel.refresh_from_db()
    assert channel.required_auth_level == WidgetAuthLevel.EMBED_KEY
    assert channel.pending_auth_level is None
    assert channel.auth_level_notified_at is None
    notify.assert_not_called()


@pytest.mark.django_db()
def test_pending_cleared_on_intermediate_downgrade_from_none_floor():
    """A grandfathered NONE channel whose widget rolls back to an intermediate level (still
    above the NONE floor, but below the pending level) must abandon the pending bump rather
    than apply a level the widget can no longer satisfy, even past the grace window. See
    ADR-0045."""
    channel = _widget_channel(
        required_auth_level=WidgetAuthLevel.NONE,
        widget_version="0.6.0",  # EMBED_KEY: above the NONE floor, below pending SESSION_TOKEN
        pending_auth_level=WidgetAuthLevel.SESSION_TOKEN,
    )
    channel.auth_level_notified_at = _past(ExperimentChannel.AUTH_LEVEL_RATCHET_GRACE + timedelta(days=1))
    channel.save(update_fields=["auth_level_notified_at"])

    with patch("apps.channels.tasks.widget_auth_level_upgrade_notification") as notify:
        ratchet_widget_auth_levels()

    channel.refresh_from_db()
    assert channel.required_auth_level == WidgetAuthLevel.NONE  # not raised to the stale pending level
    assert channel.pending_auth_level is None
    assert channel.auth_level_notified_at is None
    notify.assert_not_called()


@pytest.mark.django_db()
def test_notifications_grouped_by_team():
    channel = _widget_channel(required_auth_level=WidgetAuthLevel.EMBED_KEY, widget_version="0.9.1")
    team = channel.team
    _widget_channel(required_auth_level=WidgetAuthLevel.EMBED_KEY, widget_version="0.9.1", team=team)

    with patch("apps.channels.tasks.widget_auth_level_upgrade_notification") as notify:
        ratchet_widget_auth_levels()

    notify.assert_called_once()
    assert len(notify.call_args.kwargs["affected_chatbots"]) == 2


@pytest.mark.django_db()
def test_non_widget_channels_ignored():
    channel = ExperimentChannelFactory(platform=ChannelPlatform.TELEGRAM, required_auth_level=WidgetAuthLevel.EMBED_KEY)
    with patch("apps.channels.tasks.widget_auth_level_upgrade_notification") as notify:
        ratchet_widget_auth_levels()
    channel.refresh_from_db()
    assert channel.required_auth_level == WidgetAuthLevel.EMBED_KEY
    assert channel.auth_level_notified_at is None
    notify.assert_not_called()


@pytest.mark.django_db()
def test_model_pending_properties():
    channel = _widget_channel(
        required_auth_level=WidgetAuthLevel.EMBED_KEY,
        pending_auth_level=WidgetAuthLevel.SESSION_TOKEN,
        auth_level_notified_at=timezone.now(),
    )
    assert channel.min_widget_version == "0.5.1"
    assert channel.pending_min_widget_version == "0.9.0"
    assert channel.pending_auth_level_effective_at == (
        channel.auth_level_notified_at + ExperimentChannel.AUTH_LEVEL_RATCHET_GRACE
    )


@pytest.mark.django_db()
def test_widget_params_context_includes_min_version():
    channel = _widget_channel(
        required_auth_level=WidgetAuthLevel.EMBED_KEY,
        pending_auth_level=WidgetAuthLevel.SESSION_TOKEN,
        auth_level_notified_at=timezone.now(),
    )
    widget = WidgetParams(experiment=channel.experiment, widget_token="tok", channel=channel)
    context = widget.get_context("widget_token", "", {})["widget"]
    assert context["min_version"] == "0.5.1"
    assert context["pending_min_version"] == "0.9.0"
    assert context["pending_effective_at"] is not None


def test_upgrade_notification_message_names_min_version():
    with patch("apps.ocs_notifications.notifications.create_notification") as create:
        widget_auth_level_upgrade_notification(
            team=object(),
            affected_chatbots={"Bot": "/bot/"},
            min_version="0.9.0",
            effective_date=datetime(2026, 8, 1),
            docs_url="https://docs.example.com",
        )
    message = create.call_args.kwargs["message"]
    assert "0.9.0" in message
    assert "01 Aug 2026" in message


def _past(delta: timedelta):
    return timezone.now() - delta
