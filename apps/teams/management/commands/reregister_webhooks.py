"""Repoint a team's channel webhooks at this server.

    manage.py reregister_webhooks --team-slug=<slug>

Runs as an independent step (typically after `sync_team`) so an operator can confirm this server's
domain is right before any provider webhook is touched, and rerun it on its own without re-syncing.
"""

import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.teams.models import Team
from apps.web.meta import absolute_url

logger = logging.getLogger(__name__)

CHANNEL_SETUP_DOCS_URL = f"{settings.DOCUMENTATION_BASE_URL}{settings.DOCUMENTATION_LINKS['deploy_channels']}"

# Platforms whose inbound messages arrive via a webhook registered with the provider. Other platforms
# either have no inbound webhook (web, API, evaluations, widget) or are configured at the app level
# rather than per channel (Slack).
WEBHOOK_PLATFORMS = {
    ChannelPlatform.TELEGRAM,
    ChannelPlatform.WHATSAPP,
    ChannelPlatform.FACEBOOK,
    ChannelPlatform.SUREADHERE,
}

URL_UNAVAILABLE = "(webhook URL unavailable -- check the channel's messaging provider and chatbot links)"


@dataclass
class WebhookReregistrationReport:
    """Outcome of re-registering channel webhooks.

    ``updated``: labels of channels whose webhook was repointed automatically.
    ``manual``: (label, webhook_url) pairs for channels the operator must configure by hand.
    """

    updated: list[str]
    manual: list[tuple[str, str]]


def _channel_label(channel: ExperimentChannel) -> str:
    """Human label for the report: the chatbot name and platform. Falls back to the channel's own name
    when it has no linked experiment (team-global channels -- web, API, evaluations -- have none)."""
    name = channel.experiment.name if channel.experiment_id else channel.name
    return f"{name} / {channel.platform_enum.label}"


def reregister_webhooks(team) -> WebhookReregistrationReport:
    """Repoint all of the team's channel webhooks at this server (re-registering a correct one is
    harmless). Channels that can't be updated automatically -- unsupported provider, undeterminable
    webhook URL, or a failed provider call -- are collected for manual setup rather than raising, so
    one bad channel can't fail the whole run."""
    report = WebhookReregistrationReport(updated=[], manual=[])
    channels = ExperimentChannel.objects.filter(team=team).select_related("experiment", "messaging_provider")
    for channel in channels:
        if channel.platform_enum not in WEBHOOK_PLATFORMS:
            continue
        try:
            webhook_url = channel.webhook_url
        except Exception:  # e.g. a TurnIO channel with no linked chatbot can't derive its URL
            logger.exception("Error determining webhook URL for channel %s", channel.id)
            webhook_url = ""
        if not webhook_url:
            report.manual.append((_channel_label(channel), URL_UNAVAILABLE))
            continue
        manager = channel.get_webhook_manager()
        if not manager or not manager.supports_webhook_management:
            report.manual.append((_channel_label(channel), webhook_url))
            continue
        try:
            manager.set_incoming_webhook(channel.extra_data or {}, webhook_url)
        except Exception:
            logger.exception("Error re-registering webhook for channel %s", channel.id)
            report.manual.append((_channel_label(channel), webhook_url))
        else:
            report.updated.append(_channel_label(channel))
    return report


class Command(BaseCommand):
    help = "Repoint a team's channel webhooks at this server."

    def add_arguments(self, parser):
        parser.add_argument("--team-slug", required=True, help="Slug of the local team to update.")
        parser.add_argument(
            "--noinput",
            "--no-input",
            action="store_false",
            dest="interactive",
            help="Skip the domain confirmation prompt (for non-interactive runs).",
        )

    def handle(self, *args, **options):
        team = Team.objects.filter(slug=options["team_slug"]).first()
        if team is None:
            raise CommandError(f"No local team '{options['team_slug']}' found.")

        if options.get("interactive", True) and not self._confirm_site_url():
            raise CommandError(
                "Aborted: fix this server's domain, then re-run. Update the Site record in the Django "
                "admin (Sites), or set SITE_URL_ROOT in the environment when running with DEBUG on."
            )

        report = reregister_webhooks(team)
        self._report(report)

    def _confirm_site_url(self) -> bool:
        """Webhook URLs are built from this server's domain (the Site record, or SITE_URL_ROOT under
        DEBUG); show it so the operator can catch a wrong domain before it gets registered with every
        channel's provider."""
        self.stdout.write(
            self.style.WARNING(f"Channel webhooks will be pointed at: {absolute_url('', is_secure=True)}")
        )
        return input("Is this correct? Type 'yes' to continue: ") == "yes"

    def _report(self, report: WebhookReregistrationReport) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Webhook re-registration report"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))

        if report.updated:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("Re-registered webhooks automatically for:"))
            for label in report.updated:
                self.stdout.write(f"  - {label}")

        if report.manual:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("These channels need their webhooks re-registered manually:"))
            for label, url in report.manual:
                self.stdout.write(f"  - {label}")
                self.stdout.write(f"      {url}")
            self.stdout.write(f"  See {CHANNEL_SETUP_DOCS_URL} for channel setup instructions.")

        if not report.updated and not report.manual:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("No channels needed a webhook update."))
