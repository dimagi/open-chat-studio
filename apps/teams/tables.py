from django.conf import settings
from django.urls import reverse
from django_tables2 import Column, Table, TemplateColumn

from apps.generics import actions


def _integration_edit_url(url_name, request, record, value):
    if record["kind"] == "mcp":
        return reverse("mcp_integrations:edit", args=[request.team.slug, record["pk"]])
    return reverse("service_providers:edit", args=[request.team.slug, record["provider_type"], record["pk"]])


def _integration_delete_url(url_name, request, record, value):
    if record["kind"] == "mcp":
        return reverse("mcp_integrations:delete", args=[request.team.slug, record["pk"]])
    return reverse("service_providers:delete", args=[request.team.slug, record["provider_type"], record["pk"]])


class IntegrationsTable(Table):
    name = TemplateColumn(template_name="teams/components/integration_row_name.html", verbose_name="Name")
    category = Column(verbose_name="Category")
    provider = Column(verbose_name="Provider")
    status = TemplateColumn(template_name="teams/components/status_badge.html", verbose_name="Status", orderable=False)
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(
                url_name="service_providers:edit",
                url_factory=_integration_edit_url,
                display_condition=lambda request, record: request.user.has_perm(record["edit_perm"]),
            ),
            actions.delete_action(
                url_name="service_providers:delete",
                url_factory=_integration_delete_url,
                display_condition=lambda request, record: request.user.has_perm(record["delete_perm"]),
                confirm_message="This will remove the integration from any places it is being used",
            ),
        ]
    )

    class Meta:
        orderable = False
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        empty_text = "No integrations configured yet."


class MembersTable(Table):
    name = TemplateColumn(template_name="teams/components/member_row_name.html", verbose_name="Member")
    roles = TemplateColumn(template_name="teams/components/member_roles.html", verbose_name="Roles", orderable=False)
    status = TemplateColumn(template_name="teams/components/member_status.html", verbose_name="Status", orderable=False)
    actions = TemplateColumn(template_name="teams/components/member_row_actions.html", orderable=False)

    class Meta:
        orderable = False
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        empty_text = "No members or pending invitations."
