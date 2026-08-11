import pytest

from apps.service_providers.forms import SureAdhereMessagingConfigForm

CONFIG = {
    "client_id": "client-123",
    "client_secret": "secret-456",
    "client_scope": "https://example.onmicrosoft.com/example-app-api/.default",
    "auth_url": "https://sa.b2clogin.com/sa.onmicrosoft.com/test/oauth2/v2.0/token",
    "base_url": "https://example.com",
}


class TestSureAdhereMessagingConfigForm:
    """The webhook_secret is optional so that existing SureAdhere providers keep working after
    deploy: enforcing it on deploy day would 401 live inbound patient messages for every
    provider that has not yet had a secret arranged with SureAdhere.
    """

    def test_webhook_secret_is_optional(self):
        form = SureAdhereMessagingConfigForm(None, data=CONFIG)
        assert form.is_valid(), form.errors
        assert form.cleaned_data["webhook_secret"] == ""

    def test_webhook_secret_is_saved_when_supplied(self):
        form = SureAdhereMessagingConfigForm(None, data={**CONFIG, "webhook_secret": "s3cr3t-value"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["webhook_secret"] == "s3cr3t-value"

    def test_webhook_secret_is_stripped(self):
        form = SureAdhereMessagingConfigForm(None, data={**CONFIG, "webhook_secret": "  s3cr3t-value  "})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["webhook_secret"] == "s3cr3t-value"

    @pytest.mark.parametrize(
        "submitted",
        [pytest.param("", id="left_blank"), pytest.param("   ", id="whitespace_only")],
    )
    def test_blank_secret_on_a_pre_existing_provider_normalises_to_empty_string(self, submitted):
        """A provider saved before this field existed has no webhook_secret in its config.

        ObfuscatingMixin restores the unmasked original for an unchanged field, which would be
        None here. Storing None would defeat the `not webhook_secret` check in the view.
        """
        form = SureAdhereMessagingConfigForm(None, data={**CONFIG, "webhook_secret": submitted}, initial=CONFIG)
        assert form.is_valid(), form.errors
        assert form.cleaned_data["webhook_secret"] == ""

    def test_unchanged_masked_secret_keeps_the_original_value(self):
        """Re-saving the provider without touching the masked field must not clobber the secret."""
        form = SureAdhereMessagingConfigForm(
            None,
            data={**CONFIG, "client_secret": "secr...56", "webhook_secret": "s3cr...ue"},
            initial={**CONFIG, "webhook_secret": "s3cr3t-value"},
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["webhook_secret"] == "s3cr3t-value"
        assert form.cleaned_data["client_secret"] == "secret-456"

    def test_secret_is_obfuscated_for_display(self):
        form = SureAdhereMessagingConfigForm(None, initial={**CONFIG, "webhook_secret": "s3cr3t-value"})
        assert form["webhook_secret"].value() == "s3cr...ue"
        assert form["client_secret"].value() == "secr...56"
