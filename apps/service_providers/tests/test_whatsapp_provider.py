from unittest import mock

import httpx
import pytest

from apps.service_providers.forms import WhatsappTestMessageForm
from apps.service_providers.messaging_service import TemplateCheck
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.service_providers.tasks import sync_whatsapp_provider_task
from apps.utils.factories.service_provider_factories import MessagingProviderFactory

NUMBER_A = {
    "phone_number_id": "1020671484465717",
    "number": "+27647084804",
    "display": "+27 64 708 4804",
    "verified_name": "TenantHive",
}
NUMBER_B = {
    "phone_number_id": "9938471029384",
    "number": "+27825550134",
    "display": "+27 82 555 0134",
    "verified_name": "TenantHive Support",
}


@pytest.fixture()
def meta_provider(db):
    return MessagingProviderFactory(
        type=MessagingProviderType.meta_cloud_api,
        config={
            "business_id": "1285815180126064",
            "access_token": "token",
            "app_secret": "secret",
            "verify_token": "verify",
        },
        extra_data={"verify_token_hash": "abc123"},
    )


def _meta_401():
    request = httpx.Request("GET", "https://test")
    return httpx.HTTPStatusError(
        "unauthorized",
        request=request,
        response=httpx.Response(
            401, json={"error": {"message": "(#190) Error validating access token"}}, request=request
        ),
    )


APPROVED_TEMPLATE = {"name": "new_bot_message", "status": "APPROVED", "language": "en"}


def _service_returning(numbers, template_check=None):
    service = mock.MagicMock()
    service.get_phone_numbers.return_value = numbers
    service.check_message_template.return_value = template_check or TemplateCheck(ok=True, template=APPROVED_TEMPLATE)
    return service


@pytest.mark.django_db()
class TestSyncWhatsappNumbers:
    def test_stores_the_numbers_from_meta(self, meta_provider):
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=_service_returning([NUMBER_A])):
            added, removed = meta_provider.sync_whatsapp_numbers()

        assert (added, removed) == (1, 0)
        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_numbers == [NUMBER_A]
        assert meta_provider.whatsapp_numbers_info["state"] == "ok"
        assert meta_provider.whatsapp_numbers_info["synced_at"]

    def test_reports_numbers_added_and_removed(self, meta_provider):
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=_service_returning([NUMBER_A])):
            meta_provider.sync_whatsapp_numbers()
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=_service_returning([NUMBER_B])):
            added, removed = meta_provider.sync_whatsapp_numbers()

        assert (added, removed) == (1, 1)
        assert meta_provider.whatsapp_numbers == [NUMBER_B]

    def test_keeps_the_webhook_verify_token_hash(self, meta_provider):
        """extra_data is shared with the webhook lookup -- a sync must not clobber it."""
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=_service_returning([NUMBER_A])):
            meta_provider.sync_whatsapp_numbers()

        meta_provider.refresh_from_db()
        assert meta_provider.extra_data["verify_token_hash"] == "abc123"

    def test_marking_a_sync_pending_leaves_the_cached_numbers_alone(self, meta_provider):
        """The panel keeps showing the last known numbers while a refresh runs."""
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=_service_returning([NUMBER_A])):
            meta_provider.sync_whatsapp_numbers()

        meta_provider.mark_whatsapp_numbers_syncing()

        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_numbers == [NUMBER_A]
        assert meta_provider.whatsapp_numbers_info["state"] == "pending"
        assert meta_provider.whatsapp_numbers_info["started_at"]


@pytest.mark.django_db()
class TestWhatsappTemplateCache:
    def test_unchecked_until_someone_checks_it(self, meta_provider):
        """No backfill: a provider that predates the cache has no answer, not a wrong one."""
        assert meta_provider.whatsapp_template_info == {}
        assert meta_provider.whatsapp_template_ok is None

    def test_records_a_usable_template(self, meta_provider):
        service = _service_returning([], TemplateCheck(ok=True, template=APPROVED_TEMPLATE))
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=service):
            check = meta_provider.check_whatsapp_template()

        assert check.ok is True
        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_template_ok is True
        assert meta_provider.whatsapp_template_info["template"] == APPROVED_TEMPLATE
        assert meta_provider.whatsapp_template_info["checked_at"]

    def test_records_the_problems_when_the_template_cannot_be_sent(self, meta_provider):
        check = TemplateCheck(ok=False, problems=["No template named 'new_bot_message' exists."])
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=_service_returning([], check)):
            meta_provider.check_whatsapp_template()

        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_template_ok is False
        assert meta_provider.whatsapp_template_info["problems"] == ["No template named 'new_bot_message' exists."]

    def test_records_the_error_when_the_check_could_not_run(self, meta_provider):
        check = TemplateCheck(ok=False, error="(#190) Error validating access token")
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=_service_returning([], check)):
            meta_provider.check_whatsapp_template()

        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_template_ok is False
        assert meta_provider.whatsapp_template_info["error"] == "(#190) Error validating access token"

    def test_leaves_the_cached_numbers_and_token_hash_alone(self, meta_provider):
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=_service_returning([NUMBER_A])):
            meta_provider.sync_whatsapp_numbers()
            meta_provider.check_whatsapp_template()

        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_numbers == [NUMBER_A]
        assert meta_provider.extra_data["verify_token_hash"] == "abc123"


@pytest.mark.django_db()
class TestSyncWhatsappProviderTask:
    """One refresh covers both the numbers and the template, and neither can lose the other."""

    def test_syncs_the_numbers_and_the_template(self, meta_provider):
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=_service_returning([NUMBER_A])):
            sync_whatsapp_provider_task(meta_provider.pk)

        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_numbers == [NUMBER_A]
        assert meta_provider.whatsapp_template_ok is True

    def test_records_the_error_when_meta_rejects_the_numbers_call(self, meta_provider):
        service = _service_returning([])
        service.get_phone_numbers.side_effect = _meta_401()
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=service):
            sync_whatsapp_provider_task(meta_provider.pk)

        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_numbers_info["state"] == "error"
        assert "Error validating access token" in meta_provider.whatsapp_numbers_info["error"]
        assert meta_provider.whatsapp_template_ok is True, "the template check must still have run"

    def test_records_the_numbers_when_the_template_check_blows_up(self, meta_provider):
        service = _service_returning([NUMBER_A])
        service.check_message_template.side_effect = _meta_401()
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=service):
            sync_whatsapp_provider_task(meta_provider.pk)

        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_numbers == [NUMBER_A]
        assert meta_provider.whatsapp_template_ok is False
        assert "Error validating access token" in meta_provider.whatsapp_template_info["error"]

    def test_ignores_providers_that_are_not_meta(self, db):
        provider = MessagingProviderFactory(type=MessagingProviderType.twilio)

        with mock.patch.object(MessagingProvider, "get_messaging_service") as get_service:
            sync_whatsapp_provider_task(provider.pk)

        get_service.assert_not_called()

    def test_ignores_a_deleted_provider(self, db):
        sync_whatsapp_provider_task(9999)


@pytest.mark.django_db()
class TestPostCreateHook:
    def test_queues_a_refresh_for_meta_providers(self, meta_provider, django_capture_on_commit_callbacks):
        with (
            mock.patch("apps.service_providers.tasks.sync_whatsapp_provider_task.delay") as delay,
            django_capture_on_commit_callbacks(execute=True),
        ):
            meta_provider.run_post_create_hook()

        delay.assert_called_once_with(meta_provider.pk)
        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_numbers_info["state"] == "pending"

    def test_does_nothing_for_other_provider_types(self, db):
        provider = MessagingProviderFactory(type=MessagingProviderType.twilio)

        with mock.patch("apps.service_providers.tasks.sync_whatsapp_provider_task.delay") as delay:
            provider.run_post_create_hook()

        delay.assert_not_called()


class TestWhatsappTestMessageForm:
    """The recipient number is checked here so a bad one is a form error, not a Meta error."""

    def _form(self, to_number: str) -> WhatsappTestMessageForm:
        return WhatsappTestMessageForm(
            [NUMBER_A],
            data={"from_number_id": NUMBER_A["phone_number_id"], "to_number": to_number, "message": "hello"},
        )

    @pytest.mark.parametrize(
        ("to_number", "expected"),
        [
            pytest.param("+12125552368", "+12125552368", id="e164"),
            pytest.param("+27 81 234 5678", "+27812345678", id="spaces-are-normalised"),
        ],
    )
    def test_a_valid_number_is_normalised(self, to_number, expected):
        form = self._form(to_number)

        assert form.is_valid(), form.errors
        assert form.cleaned_data["to_number"] == expected

    @pytest.mark.parametrize(
        "to_number",
        [
            pytest.param("0812345678", id="no-country-code"),
            pytest.param("not a number", id="not-a-number"),
            pytest.param("+1234", id="parses-but-too-short"),
            pytest.param("+10000000000", id="parses-but-unassigned-range"),
        ],
    )
    def test_a_number_meta_would_reject_is_a_form_error(self, to_number):
        form = self._form(to_number)

        assert not form.is_valid()
        assert form.errors["to_number"] == ["Enter a valid phone number (e.g. +12125552368)."]


@pytest.mark.django_db()
def test_saving_the_config_form_keeps_the_cached_numbers(meta_provider):
    """Editing the provider must not wipe the number cache stored alongside the token hash."""
    with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=_service_returning([NUMBER_A])):
        meta_provider.sync_whatsapp_numbers()

    form = MessagingProviderType.meta_cloud_api.form_cls(
        team=meta_provider.team,
        data={
            "business_id": "1285815180126064",
            "access_token": "token",
            "app_secret": "secret",
            "verify_token": "verify",
            "template_language_code": "en",
        },
    )
    assert form.is_valid(), form.errors
    form.save(meta_provider)

    assert meta_provider.whatsapp_numbers == [NUMBER_A]
    assert meta_provider.extra_data["verify_token_hash"]
