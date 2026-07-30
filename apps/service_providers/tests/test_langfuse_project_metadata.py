from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

from apps.service_providers.forms import LangfuseTraceProviderForm
from apps.service_providers.models import TraceProvider
from apps.service_providers.tracing.langfuse import fetch_project_metadata
from apps.utils.factories.service_provider_factories import TraceProviderFactory

CONFIG = {"public_key": "pk-lf-1", "secret_key": "sk-lf-1", "host": "https://cloud.langfuse.com"}
METADATA = {"project_id": "proj-1", "project_name": "OCS Prod", "organization_name": "Dimagi"}


def _project(project_id="proj-1", name="OCS Prod", org_id="org-1", org_name="Dimagi", retention_days=90):
    project = MagicMock()
    project.id = project_id
    project.name = name
    project.organization.id = org_id
    project.organization.name = org_name
    project.retention_days = retention_days
    return project


def _api_client(projects):
    api = MagicMock()
    api.projects.get.return_value = MagicMock(data=projects)
    return api


class TestFetchProjectMetadata:
    def test_returns_project_and_organization_details(self):
        with patch(
            "apps.service_providers.tracing.langfuse.get_langfuse_api_client",
            return_value=_api_client([_project()]),
        ):
            metadata = fetch_project_metadata(CONFIG)

        assert metadata["project_id"] == "proj-1"
        assert metadata["project_name"] == "OCS Prod"
        assert metadata["organization_id"] == "org-1"
        assert metadata["organization_name"] == "Dimagi"
        assert metadata["retention_days"] == 90
        assert metadata["synced_at"]

    @pytest.mark.parametrize(
        "projects",
        [
            pytest.param([], id="no-projects"),
            pytest.param([_project("proj-1"), _project("proj-2")], id="organization-scoped-key"),
        ],
    )
    def test_raises_when_the_project_is_ambiguous(self, projects):
        """Only a project-scoped key pair identifies a single project; anything else we can't attribute."""
        with (
            patch(
                "apps.service_providers.tracing.langfuse.get_langfuse_api_client",
                return_value=_api_client(projects),
            ),
            pytest.raises(ValueError, match="Expected exactly one Langfuse project"),
        ):
            fetch_project_metadata(CONFIG)


class TestLangfuseTraceProviderForm:
    def test_save_populates_metadata(self):
        form = LangfuseTraceProviderForm(None, data=CONFIG)
        assert form.is_valid(), form.errors
        instance = TraceProviderFactory.build()

        with patch(
            "apps.service_providers.tracing.langfuse.fetch_project_metadata",
            return_value={"project_id": "proj-1", "organization_name": "Dimagi"},
        ):
            form.save(instance)

        assert instance.metadata == {"project_id": "proj-1", "organization_name": "Dimagi"}
        assert form.warnings == []

    def test_failed_lookup_warns_and_keeps_existing_metadata(self):
        """A Langfuse outage must not stop someone saving their tracing config."""
        form = LangfuseTraceProviderForm(None, data=CONFIG)
        assert form.is_valid(), form.errors
        instance = TraceProviderFactory.build(metadata={"project_id": "proj-old"})

        with patch(
            "apps.service_providers.tracing.langfuse.fetch_project_metadata",
            side_effect=Exception("API unreachable"),
        ):
            form.save(instance)

        assert instance.metadata == {"project_id": "proj-old"}
        assert len(form.warnings) == 1
        assert "could not be fetched" in form.warnings[0]


@pytest.mark.django_db()
class TestProviderViews:
    """The create/edit view is the real entry point: it must persist metadata and surface warnings."""

    @pytest.fixture()
    def authed_client(self, team_with_users, client):
        client.force_login(team_with_users.members.first())
        return client

    def _post(self, client, team, data, pk=None):
        kwargs = {"team_slug": team.slug, "provider_type": "tracing"}
        if pk:
            url = reverse("service_providers:edit", kwargs={**kwargs, "pk": pk})
        else:
            url = reverse("service_providers:new", kwargs={**kwargs, "subtype": "langfuse"})
        return client.post(url, data, follow=True)

    def test_creating_a_provider_records_the_project(self, team_with_users, authed_client):
        with patch(
            "apps.service_providers.tracing.langfuse.fetch_project_metadata",
            return_value=METADATA,
        ):
            response = self._post(authed_client, team_with_users, {"name": "Langfuse", **CONFIG})

        assert response.status_code == 200
        provider = TraceProvider.objects.get(team=team_with_users, name="Langfuse")
        assert provider.metadata == METADATA
        assert [m.message for m in response.context["messages"]] == []

    def test_a_failed_lookup_still_saves_and_warns(self, team_with_users, authed_client):
        with patch(
            "apps.service_providers.tracing.langfuse.fetch_project_metadata",
            side_effect=Exception("API unreachable"),
        ):
            response = self._post(authed_client, team_with_users, {"name": "Langfuse", **CONFIG})

        provider = TraceProvider.objects.get(team=team_with_users, name="Langfuse")
        assert provider.metadata == {}
        messages = [m.message for m in response.context["messages"]]
        assert len(messages) == 1
        assert "could not be fetched" in messages[0]

    def test_editing_a_provider_refreshes_the_project(self, team_with_users, authed_client):
        provider = TraceProviderFactory(team=team_with_users, config=CONFIG, metadata={"project_id": "proj-old"})
        with patch(
            "apps.service_providers.tracing.langfuse.fetch_project_metadata",
            return_value=METADATA,
        ):
            self._post(authed_client, team_with_users, {"name": provider.name, **CONFIG}, pk=provider.pk)

        provider.refresh_from_db()
        assert provider.metadata == METADATA
