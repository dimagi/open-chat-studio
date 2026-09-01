from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from apps.service_providers import messaging_service

WHATSAPP_NUMBERS_KEY = "whatsapp_numbers"
WHATSAPP_TEMPLATE_KEY = "whatsapp_template"
WHATSAPP_REFRESH_KEY = "whatsapp_refresh"


class WhatsAppProviderMixin:
    """The WhatsApp state a messaging provider caches on itself, and the calls that fill it.

    Only Meta Cloud API providers use any of this; on every other provider type the cache
    stays empty. The host model must have an ``extra_data`` JSON field with an
    ``_update_extra_data`` writer, and a ``get_messaging_service()``.
    """

    @property
    def whatsapp_numbers_info(self) -> dict:
        """The cached WhatsApp number sync: ``state``, ``synced_at``, ``error`` and ``numbers``."""
        return (self.extra_data or {}).get(WHATSAPP_NUMBERS_KEY, {})

    @property
    def whatsapp_numbers(self) -> list[dict]:
        return self.whatsapp_numbers_info.get("numbers", [])

    def resolve_whatsapp_number(self, number: str) -> dict | None:
        """Match `number` against the cached numbers, syncing first if nothing is cached.

        The check an operator makes when adding a number is what fills the cache for providers
        that predate it.
        """
        if not self.whatsapp_numbers:
            self.sync_whatsapp_numbers()
        match = next((entry for entry in self.whatsapp_numbers if entry["number"] == number), None)
        if not match:
            return None
        return {"number": number, "phone_number_id": match["phone_number_id"]}

    @property
    def whatsapp_refresh_info(self) -> dict:
        """The refresh running right now -- ``started_at`` -- or empty when none is.

        One marker covers the whole refresh rather than one per leg. The numbers and the
        template are fetched and committed separately, so watching either leg on its own would
        report the refresh finished while the other was still talking to Meta.
        """
        return (self.extra_data or {}).get(WHATSAPP_REFRESH_KEY, {})

    def mark_whatsapp_refresh_queued(self) -> None:
        """Flag a refresh as in flight. Everything cached stays put so the page can keep showing it."""
        self._update_extra_data(WHATSAPP_REFRESH_KEY, {"started_at": timezone.now().isoformat()})

    def mark_whatsapp_refresh_done(self) -> None:
        """Clear the in-flight marker, however the two legs went."""
        self._update_extra_data(WHATSAPP_REFRESH_KEY, {})

    def mark_whatsapp_numbers_failed(self, error: str) -> None:
        self._update_extra_data(WHATSAPP_NUMBERS_KEY, self.whatsapp_numbers_info | {"state": "error", "error": error})

    def sync_whatsapp_numbers(self) -> tuple[int, int]:
        """Cache this account's WhatsApp numbers and their phone number IDs.

        Returns the number of entries added and removed relative to what was cached.
        """
        numbers = self.get_messaging_service().get_phone_numbers()
        previous = {number["phone_number_id"] for number in self.whatsapp_numbers}
        status = self.whatsapp_numbers_info | {
            "state": "ok",
            "synced_at": timezone.now().isoformat(),
            "error": None,
            "numbers": numbers,
        }
        self._update_extra_data(WHATSAPP_NUMBERS_KEY, status)
        current = {number["phone_number_id"] for number in numbers}
        return len(current - previous), len(previous - current)

    @property
    def whatsapp_template_info(self) -> dict:
        """The cached message template check: ``ok``, ``checked_at``, ``problems``, ``error``, ``template``.

        Empty while the template has never been checked. Nothing backfills it, so a provider
        created before the cache existed stays empty until someone refreshes it.
        """
        return (self.extra_data or {}).get(WHATSAPP_TEMPLATE_KEY, {})

    @property
    def whatsapp_template_ok(self) -> bool | None:
        """Whether the template can be sent, or ``None`` if it has never been checked.

        The three states are all distinct to callers: a template known to be broken and one
        nobody has looked at yet warrant different warnings.
        """
        return self.whatsapp_template_info.get("ok")

    def record_whatsapp_template_check(self, check: "messaging_service.TemplateCheck") -> None:
        self._update_extra_data(
            WHATSAPP_TEMPLATE_KEY,
            {
                "ok": check.ok,
                "checked_at": timezone.now().isoformat(),
                "problems": check.problems,
                "error": check.error,
                "template": check.template,
            },
        )

    def check_whatsapp_template(self) -> "messaging_service.TemplateCheck":
        """Ask Meta whether this account can send the bot's message template, and cache the answer."""
        check = self.get_messaging_service().check_message_template()
        self.record_whatsapp_template_check(check)
        return check

    def queue_whatsapp_provider_sync(self) -> None:
        """Flag a refresh as pending and enqueue it once the current transaction commits."""
        # circular: tasks imports models
        from apps.service_providers.tasks import sync_whatsapp_provider_task  # noqa: PLC0415

        self.mark_whatsapp_refresh_queued()
        transaction.on_commit(lambda: sync_whatsapp_provider_task.delay(self.pk))
