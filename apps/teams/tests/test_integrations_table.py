import pytest
from django.test import RequestFactory
from django.urls import reverse
from waffle.testutils import override_flag

from apps.mcp_integrations.models import McpServer
from apps.teams.backends import add_user_to_team, make_user_team_owner
from apps.teams.views.integrations_views import build_integration_filter_pills, get_integration_rows
from apps.utils.factories.service_provider_factories import LlmProviderFactory, VoiceProviderFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def team():
    return TeamFactory()


@pytest.fixture()
def request_for(team):
    def _make(user):
        request = RequestFactory().get("/")
        request.team = team
        request.user = user
        return request

    return _make


@pytest.mark.django_db()
def test_get_integration_rows_composite_ids_are_prefixed_by_provider_type(team, request_for):
    """Row ids stay unique even if an LlmProvider and a VoiceProvider end up sharing a pk."""
    llm = LlmProviderFactory(team=team)
    voice = VoiceProviderFactory(team=team)

    user = UserFactory()
    rows = get_integration_rows(request_for(user), team)

    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    assert f"llm-{llm.pk}" in ids
    assert f"voice-{voice.pk}" in ids
    assert {row["category"] for row in rows} == {"LLM & embedding", "Speech"}


@pytest.mark.django_db()
def test_mcp_rows_excluded_when_flag_off(team, request_for):
    McpServer.objects.create(team=team, name="Zapier", server_url="https://example.org/mcp")
    user = UserFactory()

    rows = get_integration_rows(request_for(user), team)
    assert rows == []

    pills = build_integration_filter_pills(rows, None, show_mcp=False)
    labels = [pill.label for pill in pills]
    assert labels == ["All", "LLM & embedding", "Speech", "Messaging", "Authentication", "Tracing"]
    assert "MCP" not in labels
    assert all(pill.count == 0 for pill in pills[1:])


@pytest.mark.django_db()
@override_flag("flag_mcp", active=True)
def test_mcp_rows_included_when_flag_on(team, request_for):
    McpServer.objects.create(team=team, name="Zapier", server_url="https://example.org/mcp")
    user = UserFactory()

    rows = get_integration_rows(request_for(user), team)
    assert len(rows) == 1
    assert rows[0]["category"] == "MCP"
    assert rows[0]["name"] == "Zapier"

    pills = build_integration_filter_pills(rows, None, show_mcp=True)
    labels = [pill.label for pill in pills]
    assert labels == ["All", "LLM & embedding", "Speech", "Messaging", "Authentication", "Tracing", "MCP"]
    mcp_pill = pills[-1]
    assert mcp_pill.count == 1


@pytest.mark.django_db()
def test_integrations_table_view_filters_by_category(client, team):
    admin = UserFactory()
    make_user_team_owner(team, admin)
    LlmProviderFactory(team=team, name="OpenAI Prod")
    VoiceProviderFactory(team=team, name="Azure Voice")
    client.force_login(admin)
    url = reverse("single_team:integrations_table", args=[team.slug])

    response = client.get(url)
    assert response.status_code == 200
    assert b"OpenAI Prod" in response.content
    assert b"Azure Voice" in response.content

    response = client.get(url, {"category": "Speech"})
    assert b"Azure Voice" in response.content
    assert b"OpenAI Prod" not in response.content


@pytest.mark.django_db()
def test_integrations_table_edit_action_hidden_without_permission(client, team):
    member = UserFactory()
    add_user_to_team(team, member)  # no provider change/delete permissions granted
    LlmProviderFactory(team=team, name="Restricted Provider")
    client.force_login(member)
    url = reverse("single_team:integrations_table", args=[team.slug])

    response = client.get(url)
    assert response.status_code == 200
    assert b"Restricted Provider" in response.content
    # No add/edit/delete permission on service_providers for a plain member, so no edit link renders.
    assert b"service_providers/llm/" not in response.content
