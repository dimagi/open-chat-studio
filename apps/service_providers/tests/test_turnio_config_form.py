from apps.service_providers.forms import TurnIOMessagingConfigForm


class TestTurnIOMessagingConfigForm:
    """The hmac_secret is optional so that existing Turn providers keep working after deploy.

    See dimagi/open-chat-studio#2346 - enforcing on deploy day would drop live webhook
    traffic for every provider that has not yet copied its secret across from Turn.
    """

    def test_hmac_secret_is_optional(self):
        form = TurnIOMessagingConfigForm(None, data={"auth_token": "token123"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["hmac_secret"] == ""

    def test_hmac_secret_is_saved_when_supplied(self):
        form = TurnIOMessagingConfigForm(None, data={"auth_token": "token123", "hmac_secret": "s3cr3t-value"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["hmac_secret"] == "s3cr3t-value"

    def test_hmac_secret_is_stripped(self):
        form = TurnIOMessagingConfigForm(None, data={"auth_token": "token123", "hmac_secret": "  s3cr3t-value  "})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["hmac_secret"] == "s3cr3t-value"

    def test_blank_secret_on_a_pre_existing_provider_normalises_to_empty_string(self):
        """A provider saved before this field existed has no hmac_secret in its config.

        ObfuscatingMixin restores the unmasked original for an unchanged field, which
        would be None here. Storing None would defeat the `not hmac_secret` check.
        """
        form = TurnIOMessagingConfigForm(
            None,
            data={"auth_token": "token123", "hmac_secret": ""},
            initial={"auth_token": "token123"},
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["hmac_secret"] == ""

    def test_unchanged_masked_secret_keeps_the_original_value(self):
        form = TurnIOMessagingConfigForm(
            None,
            data={"auth_token": "token123", "hmac_secret": "s3cr...ue"},
            initial={"auth_token": "token123", "hmac_secret": "s3cr3t-value"},
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["hmac_secret"] == "s3cr3t-value"

    def test_secret_is_obfuscated_for_display(self):
        form = TurnIOMessagingConfigForm(None, initial={"auth_token": "token123", "hmac_secret": "s3cr3t-value"})
        assert form["hmac_secret"].value() == "s3cr...ue"
