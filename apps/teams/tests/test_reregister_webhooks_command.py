import io

import pytest
from django.core.management.base import CommandError

from apps.channels.models import ChannelPlatform
from apps.service_providers.models import MessagingProviderType
from apps.teams.management.commands.reregister_webhooks import (
    CHANNEL_SETUP_DOCS_URL,
    Command,
    WebhookReregistrationReport,
    reregister_webhooks,
)
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory

pytestmark = pytest.mark.django_db


def _always_raises(*args, **kwargs):
    raise RuntimeError("provider unreachable")


def test_reregister_webhooks_registers_supported_channel(monkeypatch):
    """A channel whose manager can manage webhooks has its webhook (re)pointed at this server and is
    reported as updated."""
    channel = ExperimentChannelFactory(platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "tok"})
    registered = []
    monkeypatch.setattr(
        "apps.channels.webhooks.TelegramWebhookManager.set_incoming_webhook",
        lambda self, extra_data, webhook_url: registered.append(webhook_url),
    )

    report = reregister_webhooks(channel.team)

    assert registered == [channel.webhook_url]  # webhook actually re-registered at the provider
    assert len(report.updated) == 1
    assert report.manual == []


def test_reregister_webhooks_flags_unsupported_channel_for_manual_setup():
    """A provider-backed channel whose service can't manage webhooks is flagged for manual setup,
    carrying the URL the operator must configure."""
    provider = MessagingProviderFactory(
        type=MessagingProviderType.meta_cloud_api, config={"access_token": "x", "business_id": "1"}
    )
    channel = ExperimentChannelFactory(
        platform=ChannelPlatform.WHATSAPP, messaging_provider=provider, extra_data={"number": "+123"}
    )

    report = reregister_webhooks(channel.team)

    assert report.updated == []
    assert report.manual == [(f"{channel.experiment.name} / {ChannelPlatform.WHATSAPP.label}", channel.webhook_url)]


def test_reregister_webhooks_skips_channels_without_a_webhook():
    """Web/API-style channels have no upstream webhook to register; they land in neither bucket."""
    channel = ExperimentChannelFactory(platform=ChannelPlatform.WEB, messaging_provider=None, extra_data={})

    report = reregister_webhooks(channel.team)

    assert report.updated == []
    assert report.manual == []


def test_reregister_webhooks_skips_slack_channels():
    """Slack has no per-channel provider webhook (the Slack app is configured separately), so a Slack
    channel must not produce a bogus manual entry pointing at the bare server root."""
    provider = MessagingProviderFactory(
        type=MessagingProviderType.slack, config={"slack_team_id": "T1", "slack_installation_id": 1}
    )
    channel = ExperimentChannelFactory(
        platform=ChannelPlatform.SLACK, messaging_provider=provider, extra_data={"slack_channel_id": "C1"}
    )

    report = reregister_webhooks(channel.team)

    assert report.updated == []
    assert report.manual == []


def test_reregister_webhooks_flags_channel_with_missing_provider():
    """A webhook-needing channel whose messaging provider is gone (deleted or not yet synced) can't
    compute its webhook URL; it must surface in the manual list rather than silently disappear."""
    channel = ExperimentChannelFactory(
        platform=ChannelPlatform.WHATSAPP, messaging_provider=None, extra_data={"number": "+123"}
    )

    report = reregister_webhooks(channel.team)

    assert report.updated == []
    assert len(report.manual) == 1
    label, url = report.manual[0]
    assert label == f"{channel.experiment.name} / {ChannelPlatform.WHATSAPP.label}"
    assert "unavailable" in url


def test_reregister_webhooks_survives_channel_whose_webhook_url_raises():
    """A TurnIO channel with no linked experiment crashes the webhook_url property (it derives the URL
    from the experiment); the channel must land in the manual list, not abort the whole run."""
    provider = MessagingProviderFactory(type=MessagingProviderType.turnio, config={"auth_token": "tok"})
    channel = ExperimentChannelFactory(
        platform=ChannelPlatform.WHATSAPP,
        experiment=None,
        name="Orphan TurnIO",
        messaging_provider=provider,
        extra_data={"number": "+123"},
    )

    report = reregister_webhooks(channel.team)

    assert report.updated == []
    assert len(report.manual) == 1
    label, url = report.manual[0]
    assert label == f"Orphan TurnIO / {ChannelPlatform.WHATSAPP.label}"
    assert "unavailable" in url


def test_reregister_webhooks_labels_channel_without_experiment_by_name(monkeypatch):
    """A channel with a webhook but no linked experiment (e.g. a team-global channel) is reported by
    its own name rather than crashing on the missing experiment."""
    channel = ExperimentChannelFactory(
        platform=ChannelPlatform.TELEGRAM, experiment=None, name="Orphan channel", extra_data={"bot_token": "tok"}
    )
    monkeypatch.setattr(
        "apps.channels.webhooks.TelegramWebhookManager.set_incoming_webhook",
        lambda self, extra_data, webhook_url: None,
    )

    report = reregister_webhooks(channel.team)

    assert report.updated == [f"Orphan channel / {ChannelPlatform.TELEGRAM.label}"]
    assert report.manual == []


def test_reregister_webhooks_flags_channel_when_registration_fails(monkeypatch):
    """A failure talking to the provider is downgraded to a manual-setup entry, not raised -- the
    command as a whole must still complete."""
    channel = ExperimentChannelFactory(platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "tok"})
    monkeypatch.setattr("apps.channels.webhooks.TelegramWebhookManager.set_incoming_webhook", _always_raises)

    report = reregister_webhooks(channel.team)

    assert report.updated == []
    assert report.manual == [(f"{channel.experiment.name} / {ChannelPlatform.TELEGRAM.label}", channel.webhook_url)]


def test_report_lists_manual_channels_and_docs_link():
    """The report names the auto-updated channels and, when any need manual setup, lists them with
    their URL and a link to the channel-setup docs."""
    out = io.StringIO()
    command = Command(stdout=out)
    report = WebhookReregistrationReport(
        updated=["Bot A / Telegram"],
        manual=[("Bot B / WhatsApp", "https://example.com/hook")],
    )

    command._report(report)

    text = out.getvalue()
    assert "Bot A / Telegram" in text
    assert "Bot B / WhatsApp" in text
    assert "https://example.com/hook" in text
    assert CHANNEL_SETUP_DOCS_URL in text


def test_raises_when_team_not_found():
    with pytest.raises(CommandError, match="No local team"):
        Command().handle(team_slug="does-not-exist", interactive=False)


def test_aborts_when_site_url_confirmation_declined(monkeypatch):
    """An operator who says the domain shown is wrong must get an aborted run and no webhook calls,
    so a wrong domain can't get baked into every provider's webhook config."""
    channel = ExperimentChannelFactory(platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "tok"})
    monkeypatch.setattr("builtins.input", lambda *a, **k: "no")
    monkeypatch.setattr(
        "apps.channels.webhooks.TelegramWebhookManager.set_incoming_webhook",
        lambda self, extra_data, webhook_url: pytest.fail("webhook registered despite declined confirmation"),
    )

    with pytest.raises(CommandError, match="Site record|SITE_URL_ROOT"):
        Command().handle(team_slug=channel.team.slug, interactive=True)


def test_confirms_site_url_and_continues_when_accepted(monkeypatch):
    channel = ExperimentChannelFactory(platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "tok"})
    registered = []
    monkeypatch.setattr("builtins.input", lambda *a, **k: "yes")
    monkeypatch.setattr(
        "apps.channels.webhooks.TelegramWebhookManager.set_incoming_webhook",
        lambda self, extra_data, webhook_url: registered.append(webhook_url),
    )
    out = io.StringIO()

    Command(stdout=out).handle(team_slug=channel.team.slug, interactive=True)

    assert registered == [channel.webhook_url]
    assert "Channel webhooks will be pointed at" in out.getvalue()


def test_noinput_skips_confirmation(monkeypatch):
    """--no-input runs without prompting, for non-interactive (CI) runs."""
    channel = ExperimentChannelFactory(platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "tok"})
    registered = []
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("prompted despite --no-input"))
    monkeypatch.setattr(
        "apps.channels.webhooks.TelegramWebhookManager.set_incoming_webhook",
        lambda self, extra_data, webhook_url: registered.append(webhook_url),
    )

    Command().handle(team_slug=channel.team.slug, interactive=False)

    assert registered == [channel.webhook_url]
