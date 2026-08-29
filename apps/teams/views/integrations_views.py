from collections import Counter
from dataclasses import dataclass

from django.urls import reverse
from django_tables2 import SingleTableView
from waffle import flag_is_active

from apps.mcp_integrations.models import McpServer
from apps.service_providers.utils import ServiceProvider, get_available_subtypes
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.tables import IntegrationsTable

MCP_CATEGORY = "MCP"

_CATEGORY_ORDER = ["LLM & embedding", "Speech", "Messaging", "Authentication", "Tracing", MCP_CATEGORY]

_CATEGORY_ICONS = {
    "LLM & embedding": "fa-brain",
    "Speech": "fa-microphone",
    "Messaging": "fa-comment-sms",
    "Authentication": "fa-key",
    "Tracing": "fa-magnifying-glass-location",
    MCP_CATEGORY: "fa-server",
}


def get_integration_rows(request, team) -> list[dict]:
    """One row per configured integration, across all provider types plus MCP servers.

    Each row's `id` is a composite key (`{provider_slug}-{pk}`) because provider pks are
    not unique across the five underlying models -- an `LlmProvider` and a `VoiceProvider`
    can share the same pk.
    """
    rows = []
    for provider in ServiceProvider:
        for obj in provider.model.objects.filter(team=team):
            rows.append(
                {
                    "id": f"{provider.slug}-{obj.pk}",
                    "kind": "service_provider",
                    "provider_type": provider.slug,
                    "pk": obj.pk,
                    "name": obj.name,
                    "category": provider.category,
                    "icon_class": _CATEGORY_ICONS[provider.category],
                    "provider": obj.type_enum.label,
                    "status": "Connected",
                    "edit_perm": provider.get_permission("change"),
                    "delete_perm": provider.get_permission("delete"),
                }
            )
    if flag_is_active(request, "flag_mcp"):
        for obj in McpServer.objects.filter(team=team):
            rows.append(
                {
                    "id": f"mcp-{obj.pk}",
                    "kind": "mcp",
                    "pk": obj.pk,
                    "name": obj.name,
                    "category": MCP_CATEGORY,
                    "icon_class": _CATEGORY_ICONS[MCP_CATEGORY],
                    "provider": "MCP Server",
                    "status": "Connected",
                    "edit_perm": "mcp_integrations.change_mcpserver",
                    "delete_perm": "mcp_integrations.delete_mcpserver",
                }
            )
    rows.sort(key=lambda row: (row["category"], row["name"]))
    return rows


def get_integration_new_choices(request, team) -> list[tuple[str, str]]:
    """(label, url) pairs for the "Add integration" dropdown, across every provider type
    the user can add, plus MCP once `flag_mcp` is on. Mirrors the permission check each
    provider's own (now-retired) per-section "Add new" dropdown used to apply."""
    choices = []
    for provider in ServiceProvider:
        if not request.user.has_perm(provider.get_permission("add")):
            continue
        for subtype in get_available_subtypes(provider, request):
            url = reverse(
                "service_providers:new",
                kwargs={"team_slug": team.slug, "provider_type": provider.slug, "subtype": str(subtype)},
            )
            choices.append((f"{provider.category}: {subtype.label}", url))
    if flag_is_active(request, "flag_mcp") and request.user.has_perm("mcp_integrations.add_mcpserver"):
        choices.append(("MCP: MCP Server", reverse("mcp_integrations:new", args=[team.slug])))
    return choices


@dataclass
class IntegrationFilterPill:
    label: str
    value: str | None
    count: int
    active: bool


def build_integration_filter_pills(
    rows: list[dict], active_category: str | None, *, show_mcp: bool = False
) -> list[IntegrationFilterPill]:
    """Always shows every provider-type category, even at a count of 0, matching the mockup
    (e.g. "Speech 0"). MCP is the exception: it's only a real category once `flag_mcp` is on."""
    counts = Counter(row["category"] for row in rows)
    categories = [category for category in _CATEGORY_ORDER if category != MCP_CATEGORY or show_mcp]
    pills = [IntegrationFilterPill(label="All", value=None, count=len(rows), active=active_category is None)]
    for category in categories:
        pills.append(
            IntegrationFilterPill(
                label=category, value=category, count=counts[category], active=active_category == category
            )
        )
    return pills


class IntegrationsTableView(LoginAndTeamRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    """Lists every configured integration for the team, across all provider types.

    No `PermissionRequiredMixin`: viewing any of the underlying provider sections has
    never had a view-level permission check on this page (only add/edit/delete do), and
    a merged table with one model per row has no single permission to require.
    """

    table_class = IntegrationsTable
    template_name = "teams/components/integrations_table.html"

    def get_queryset(self):
        rows = get_integration_rows(self.request, self.request.team)
        category = self.request.GET.get("category")
        if category:
            rows = [row for row in rows if row["category"] == category]
        return rows

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_rows = get_integration_rows(self.request, self.request.team)
        context["filter_pills"] = build_integration_filter_pills(
            all_rows, self.request.GET.get("category"), show_mcp=flag_is_active(self.request, "flag_mcp")
        )
        context["table_url"] = self.request.path
        return context
