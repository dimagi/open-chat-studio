import json
import logging
from functools import cached_property

import phonenumbers
from django import forms
from django.urls import reverse

from apps.channels.forms.base import WebhookUrlFormBase
from apps.channels.models import ExperimentChannel
from apps.service_providers.forms import whatsapp_number_label
from apps.service_providers.models import MessagingProvider, MessagingProviderType

logger = logging.getLogger("ocs.channels")

NUMBER_NOT_FOUND = "{number} was not found at the provider. Please make sure it is there before proceeding"


class WhatsappChannelForm(WebhookUrlFormBase):
    custom_template = "channels/partials/whatsapp_channel_fields.html"

    number = forms.CharField(
        label="Number",
        max_length=20,
        help_text=(
            "This is the WhatsApp Business Number you got from your provider and should be in any of the formats: "
            "+27812345678, +27-81-234-5678, +27 81 234 5678"
        ),
    )

    @property
    def form_attrs(self) -> dict:
        """Hand the modal every Meta provider's cached numbers up front.

        Switching provider then swaps the number control client-side, with no request. This is
        evaluated at render time so numbers a sync fetched during validation are already here.
        """
        return {
            "x-data": json.dumps(
                {
                    "numbersByProvider": self._numbers_by_provider,
                    "providerId": self._selected_provider_id(),
                    "number": self._submitted_number() or "",
                }
            ),
            "x-on:change.document": (
                "if ($event.target.name === 'messaging_provider') providerId = $event.target.value"
            ),
        }

    @property
    def has_cached_numbers(self) -> bool:
        """Whether the provider selected right now has numbers, for the pre-Alpine render."""
        provider_id = self._selected_provider_id()
        return bool(self._numbers_by_provider.get(provider_id, {}).get("numbers"))

    def _selected_provider_id(self) -> str:
        if provider_id := self.data.get("messaging_provider"):
            return str(provider_id)
        if self.channel and self.channel.messaging_provider_id:
            return str(self.channel.messaging_provider_id)
        return ""

    def _submitted_number(self) -> str | None:
        if self.is_bound:
            return self.data.get("number")
        return (self.channel.extra_data or {}).get("number") if self.channel else None

    @cached_property
    def _numbers_by_provider(self) -> dict:
        """The number options and provider page URL for each of the team's Meta providers.

        Cached because one render reads it through both `form_attrs` and `has_cached_numbers`,
        and it is a query every time. Nothing reads it before validation, so a sync that runs
        during `clean()` is still reflected here.
        """
        team = self.experiment.team
        saved_number = (self.channel.extra_data or {}).get("number") if self.channel else None
        options = {}
        for provider in MessagingProvider.objects.filter(team=team, type=MessagingProviderType.meta_cloud_api):
            numbers = [
                {"value": number["number"], "label": whatsapp_number_label(number)}
                for number in provider.whatsapp_numbers
                if number.get("number")
            ]
            # Keep a number the channel already uses selectable, even if Meta no longer lists it.
            is_channels_provider = bool(self.channel) and self.channel.messaging_provider_id == provider.id
            if saved_number and is_channels_provider and saved_number not in {n["value"] for n in numbers}:
                numbers.append({"value": saved_number, "label": saved_number})
            options[str(provider.id)] = {
                "numbers": numbers,
                "provider_url": reverse(
                    "service_providers:edit",
                    kwargs={"team_slug": team.slug, "provider_type": "messaging", "pk": provider.id},
                ),
            }
        return options

    def clean_number(self):
        try:
            number_obj = phonenumbers.parse(self.cleaned_data["number"])
        except phonenumbers.NumberParseException:
            raise forms.ValidationError("Enter a valid phone number (e.g. +12125552368).") from None
        # Parsing only checks the shape, so reject numbers that cannot be dialled before we
        # go looking for them at the provider.
        if not phonenumbers.is_valid_number(number_obj):
            raise forms.ValidationError("Enter a valid phone number (e.g. +12125552368).")
        return phonenumbers.format_number(number_obj, phonenumbers.PhoneNumberFormat.E164)

    def post_save(self, channel: ExperimentChannel):
        self.configure_webhook(channel)

    def clean(self):
        cleaned_data = super().clean()
        number = cleaned_data.get("number")
        provider = self.messaging_provider
        if not number or not provider:
            return cleaned_data

        if unchanged := self._unchanged_number_config(provider, number):
            cleaned_data.update(unchanged)
            return cleaned_data

        try:
            resolved = provider.resolve_number(number)
        except Exception:
            logger.exception("Could not resolve number at messaging provider %s", provider.id)
            self.add_error("number", "Could not validate this number right now. Please try again.")
            return cleaned_data

        if resolved is None:
            self.add_error("number", NUMBER_NOT_FOUND.format(number=number))
            return cleaned_data

        cleaned_data.update(resolved)
        return cleaned_data

    def _unchanged_number_config(self, provider: MessagingProvider, number: str) -> dict | None:
        """The config already saved for this number, when an edit leaves it and the provider alone.

        The picker keeps a saved number selectable after the provider stops listing it, so
        re-checking one here would block every later edit to the channel -- a rename included --
        on a number the operator cannot change from this form. It was resolved when it was saved.
        """
        if not self.channel or self.channel.messaging_provider_id != provider.id:
            return None
        saved = self.channel.extra_data or {}
        if saved.get("number") != number:
            return None
        config = {"number": number}
        if phone_number_id := saved.get("phone_number_id"):
            config["phone_number_id"] = phone_number_id
        return config
