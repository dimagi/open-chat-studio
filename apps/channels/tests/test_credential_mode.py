from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from field_audit.models import AuditAction

from apps.channels.models import ChannelPlatform, CredentialMode, ExperimentChannel, WidgetAuthLevel
from apps.utils.factories.channels import ExperimentChannelFactory


def _widget_channel(**kwargs):
    kwargs.setdefault("platform", ChannelPlatform.EMBEDDED_WIDGET)
    kwargs.setdefault("extra_data", {"allowed_domains": ["example.com"]})
    return ExperimentChannelFactory(**kwargs)


@pytest.mark.django_db()
def test_new_channel_defaults_to_embed_key():
    """Every existing channel migrates to this mode, so it must be today's behaviour."""
    assert _widget_channel().credential_mode == CredentialMode.EMBED_KEY


def test_embedded_widget_label_covers_the_api():
    assert ChannelPlatform.EMBEDDED_WIDGET.label == "Chat Widget & API"


@pytest.mark.django_db()
def test_oauth_mode_pins_auth_level_on_save():
    """An oauth channel below SESSION_TOKEN issues no token, so every follow-up 403s (D1)."""
    channel = _widget_channel(credential_mode=CredentialMode.OAUTH, required_auth_level=WidgetAuthLevel.EMBED_KEY)
    channel.refresh_from_db()
    assert channel.required_auth_level == WidgetAuthLevel.SESSION_TOKEN


@pytest.mark.django_db()
def test_switching_to_oauth_clears_a_pending_ratchet():
    channel = _widget_channel(required_auth_level=WidgetAuthLevel.NONE)
    ExperimentChannel.objects.filter(pk=channel.pk).update(
        pending_auth_level=WidgetAuthLevel.SESSION_TOKEN,
        auth_level_notified_at=timezone.now(),
        audit_action=AuditAction.IGNORE,
    )
    channel.refresh_from_db()

    channel.credential_mode = CredentialMode.OAUTH
    channel.save()

    channel.refresh_from_db()
    assert channel.required_auth_level == WidgetAuthLevel.SESSION_TOKEN
    assert channel.pending_auth_level is None
    assert channel.auth_level_notified_at is None


@pytest.mark.django_db()
def test_clean_rejects_oauth_below_session_token():
    channel = _widget_channel()
    channel.credential_mode = CredentialMode.OAUTH
    channel.required_auth_level = WidgetAuthLevel.EMBED_KEY
    with pytest.raises(ValidationError) as exc_info:
        channel.clean()
    assert "required_auth_level" in exc_info.value.message_dict


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "level",
    [
        pytest.param(WidgetAuthLevel.NONE, id="none"),
        pytest.param(WidgetAuthLevel.EMBED_KEY, id="embed-key"),
    ],
)
def test_constraint_makes_the_bad_pair_unrepresentable(level):
    """save() pins it, but the DB is what guarantees no row can hold the dead combination."""
    channel = _widget_channel(credential_mode=CredentialMode.OAUTH)
    with pytest.raises(IntegrityError), transaction.atomic():
        ExperimentChannel.objects.filter(pk=channel.pk).update(
            required_auth_level=level, audit_action=AuditAction.IGNORE
        )


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "level",
    [
        pytest.param(WidgetAuthLevel.NONE, id="none"),
        pytest.param(WidgetAuthLevel.EMBED_KEY, id="embed-key"),
        pytest.param(WidgetAuthLevel.SESSION_TOKEN, id="session-token"),
    ],
)
def test_embed_key_mode_allows_every_level(level):
    channel = _widget_channel(required_auth_level=level)
    channel.refresh_from_db()
    assert channel.required_auth_level == level


@pytest.mark.django_db()
def test_session_token_lifetime_defaults_to_null():
    """Null means 'use the global', so no channel's expiry changes on migration."""
    assert _widget_channel().session_token_lifetime is None


@pytest.mark.django_db()
def test_session_token_lifetime_round_trips():
    channel = _widget_channel(session_token_lifetime=timedelta(hours=4))
    channel.refresh_from_db()
    assert channel.session_token_lifetime == timedelta(hours=4)


@pytest.mark.django_db()
def test_partial_save_still_carries_the_pin():
    """A save(update_fields=...) that omits the level must not leave the row in the forbidden pair."""
    channel = _widget_channel()
    ExperimentChannel.objects.filter(pk=channel.pk).update(
        required_auth_level=WidgetAuthLevel.EMBED_KEY, audit_action=AuditAction.IGNORE
    )
    channel.refresh_from_db()

    channel.credential_mode = CredentialMode.OAUTH
    channel.save(update_fields=["credential_mode"])

    channel.refresh_from_db()
    assert channel.required_auth_level == WidgetAuthLevel.SESSION_TOKEN
