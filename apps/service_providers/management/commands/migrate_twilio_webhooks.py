from urllib.parse import urlparse, urlunparse

from django.core.management.base import BaseCommand
from twilio.base.exceptions import TwilioException

from apps.service_providers.messaging_service import TwilioSenderWebhookUpdate
from apps.service_providers.models import MessagingProvider, MessagingProviderType

LEGACY_TWILIO_PATH = "/channels/whatsapp/incoming_message"
CURRENT_TWILIO_PATH = "/channels/twilio/incoming_message"


class Command(BaseCommand):
    help = (
        "Migrate Twilio webhook URLs (WhatsApp sender webhooks and Messaging Service inbound URLs) "
        "that point at an old domain to a new domain. Dry run by default; pass --apply to make changes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--new-domain", required=True, help="Domain to point webhooks at")
        parser.add_argument("--old-domain", default="chatbots.dimagi.com", help="Domain to migrate away from")
        parser.add_argument("--apply", action="store_true", help="Make the changes (default is a dry run)")

    def handle(self, *args, **options):
        self.old_domain = options["old_domain"]
        self.new_domain = options["new_domain"]
        self.apply = options["apply"]
        if not self.apply:
            self.stdout.write(self.style.WARNING("Dry run. Pass --apply to make changes."))

        # multiple providers can be configured with the same Twilio account; only process each resource once
        self.seen_senders = set()
        self.seen_services = set()

        providers = MessagingProvider.objects.filter(type=MessagingProviderType.twilio).select_related("team")
        for provider in providers:
            self.stdout.write(f"Provider '{provider.name}' (team: {provider.team.slug})")
            try:
                client = provider.get_messaging_service().client
                self._migrate_senders(client)
                self._migrate_services(client)
            except TwilioException as e:
                self.stderr.write(self.style.ERROR(f"  Twilio API error: {e}"))

    def _migrate_senders(self, client):
        for sender in client.messaging.v2.channels_senders.list(channel="whatsapp"):
            if sender.sid in self.seen_senders:
                continue
            self.seen_senders.add(sender.sid)
            webhook = sender.webhook or {}
            updated = self._migrate_urls(webhook)
            if not updated:
                continue
            self._report(f"sender {sender.sender_id}", webhook, updated)
            if self.apply:
                payload = {key: value for key, value in (webhook | updated).items() if value is not None}
                client.messaging.v2.channels_senders(sender.sid).update(
                    messaging_v2_channels_sender_requests_update=TwilioSenderWebhookUpdate(payload)
                )

    def _migrate_services(self, client):
        for service in client.messaging.v1.services.list():
            if service.sid in self.seen_services:
                continue
            self.seen_services.add(service.sid)
            urls = {
                field: getattr(service, field) for field in ("inbound_request_url", "fallback_url", "status_callback")
            }
            updated = self._migrate_urls(urls)
            if not updated:
                continue
            self._report(f"messaging service '{service.friendly_name}' ({service.sid})", urls, updated)
            if self.apply:
                service.update(**updated)

    def _migrate_urls(self, fields: dict) -> dict:
        """Return the subset of `fields` whose URL needs migrating, with the new value."""
        updated = {}
        for key, value in fields.items():
            if not value:
                continue
            new_url = self._migrate_url(value)
            if new_url != value:
                updated[key] = new_url
        return updated

    def _migrate_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.netloc == self.old_domain:
            parsed = parsed._replace(netloc=self.new_domain)
        # `whatsapp/incoming_message` is a legacy alias for `twilio/incoming_message` (see apps/channels/urls.py)
        if parsed.netloc == self.new_domain and parsed.path == LEGACY_TWILIO_PATH:
            parsed = parsed._replace(path=CURRENT_TWILIO_PATH)
        return urlunparse(parsed)

    def _report(self, label, current, updated):
        action = "Updating" if self.apply else "Would update"
        self.stdout.write(f"  {action} {label}:")
        for key, new_url in updated.items():
            self.stdout.write(f"    {key}: {current[key]} -> {new_url}")
