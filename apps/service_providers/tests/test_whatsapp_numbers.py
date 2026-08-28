from unittest import mock

import httpx
import pytest

from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.service_providers.tasks import sync_whatsapp_numbers_task
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


def _service_returning(numbers):
    service = mock.MagicMock()
    service.get_phone_numbers.return_value = numbers
    return service


@pytest.mark.django_db()
class TestSyncWhatsappNumbers:
    def test_stores_the_numbers_from_meta(self, meta_provider):
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=_service_returning([NUMBER_A])):
            added, removed = meta_provider.sync_whatsapp_numbers()

        assert (added, removed) == (1, 0)
        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_numbers == [NUMBER_A]
        assert meta_provider.whatsapp_numbers_status["state"] == "ok"
        assert meta_provider.whatsapp_numbers_status["synced_at"]

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
        assert meta_provider.whatsapp_numbers_status["state"] == "pending"
        assert meta_provider.whatsapp_numbers_status["started_at"]


@pytest.mark.django_db()
class TestSyncWhatsappNumbersTask:
    def test_syncs_the_provider(self, meta_provider):
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=_service_returning([NUMBER_A])):
            sync_whatsapp_numbers_task(meta_provider.pk)

        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_numbers == [NUMBER_A]

    def test_records_the_error_when_meta_rejects_the_call(self, meta_provider):
        service = mock.MagicMock()
        service.get_phone_numbers.side_effect = httpx.HTTPStatusError(
            "unauthorized",
            request=httpx.Request("GET", "https://test"),
            response=httpx.Response(
                401,
                json={"error": {"message": "(#190) Error validating access token"}},
                request=httpx.Request("GET", "https://test"),
            ),
        )
        with mock.patch.object(MessagingProvider, "get_messaging_service", return_value=service):
            sync_whatsapp_numbers_task(meta_provider.pk)

        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_numbers_status["state"] == "error"
        assert "Error validating access token" in meta_provider.whatsapp_numbers_status["error"]

    def test_ignores_providers_that_are_not_meta(self, db):
        provider = MessagingProviderFactory(type=MessagingProviderType.twilio)

        with mock.patch.object(MessagingProvider, "get_messaging_service") as get_service:
            sync_whatsapp_numbers_task(provider.pk)

        get_service.assert_not_called()

    def test_ignores_a_deleted_provider(self, db):
        sync_whatsapp_numbers_task(9999)


@pytest.mark.django_db()
class TestPostCreateHook:
    def test_queues_a_number_sync_for_meta_providers(self, meta_provider, django_capture_on_commit_callbacks):
        with (
            mock.patch("apps.service_providers.tasks.sync_whatsapp_numbers_task.delay") as delay,
            django_capture_on_commit_callbacks(execute=True),
        ):
            meta_provider.run_post_create_hook()

        delay.assert_called_once_with(meta_provider.pk)
        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_numbers_status["state"] == "pending"

    def test_does_nothing_for_other_provider_types(self, db):
        provider = MessagingProviderFactory(type=MessagingProviderType.twilio)

        with mock.patch("apps.service_providers.tasks.sync_whatsapp_numbers_task.delay") as delay:
            provider.run_post_create_hook()

        delay.assert_not_called()


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
