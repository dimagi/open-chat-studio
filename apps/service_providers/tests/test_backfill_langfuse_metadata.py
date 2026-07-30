from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

from apps.utils.factories.service_provider_factories import TraceProviderFactory

CONFIG = {"public_key": "pk-lf-1", "secret_key": "sk-lf-1", "host": "https://cloud.langfuse.com"}
METADATA = {"project_id": "proj-1", "project_name": "OCS Prod", "organization_name": "Dimagi"}
FETCH = "apps.service_providers.management.commands.backfill_langfuse_metadata.fetch_project_metadata"


@pytest.mark.django_db()
class TestBackfillCommand:
    def _run(self, *args):
        out, err = StringIO(), StringIO()
        call_command("backfill_langfuse_metadata", *args, stdout=out, stderr=err)
        return out.getvalue() + err.getvalue()

    def test_populates_providers_without_metadata(self):
        provider = TraceProviderFactory()
        with patch(FETCH, return_value=METADATA):
            output = self._run()

        provider.refresh_from_db()
        assert provider.metadata == METADATA
        assert "Updated: 1" in output

    def test_skips_providers_that_already_have_details(self):
        provider = TraceProviderFactory(metadata={"project_id": "proj-old"})
        with patch(FETCH) as fetch:
            output = self._run()

        fetch.assert_not_called()
        provider.refresh_from_db()
        assert provider.metadata == {"project_id": "proj-old"}
        assert "skipped: 1" in output

    def test_force_refetches_populated_providers(self):
        provider = TraceProviderFactory(metadata={"project_id": "proj-old"})
        with patch(FETCH, return_value=METADATA):
            self._run("--force")

        provider.refresh_from_db()
        assert provider.metadata["project_id"] == "proj-1"

    def test_dry_run_writes_nothing(self):
        provider = TraceProviderFactory()
        with patch(FETCH, return_value=METADATA):
            output = self._run("--dry-run")

        provider.refresh_from_db()
        assert provider.metadata == {}
        assert "Would update: 1" in output

    def test_a_failure_does_not_stop_the_rest(self):
        """Providers are fetched one at a time so a single bad key pair can't strand the rest."""
        failing = TraceProviderFactory(config={**CONFIG, "public_key": "pk-lf-fails"})
        succeeding = TraceProviderFactory(config={**CONFIG, "public_key": "pk-lf-works"})

        def fake_fetch(config):
            if config["public_key"] == "pk-lf-fails":
                raise Exception("API unreachable")
            return METADATA

        with patch(FETCH, side_effect=fake_fetch):
            output = self._run()

        succeeding.refresh_from_db()
        failing.refresh_from_db()
        assert succeeding.metadata == METADATA
        assert failing.metadata == {}
        assert "Updated: 1" in output
        assert "failed: 1" in output

    def test_filters_by_provider_id(self):
        target = TraceProviderFactory()
        other = TraceProviderFactory()
        with patch(FETCH, return_value=METADATA):
            self._run("--provider-id", str(target.id))

        target.refresh_from_db()
        other.refresh_from_db()
        assert target.metadata == METADATA
        assert other.metadata == {}
