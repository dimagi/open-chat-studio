from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.mcp_integrations.forms import McpServerForm
from apps.mcp_integrations.models import McpServer
from apps.mcp_integrations.tables import McpServerTable
from apps.mcp_integrations.tasks import sync_tools_task
from apps.teams.mixins import LoginAndTeamRequiredMixin


class McpServerHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        return {
            "active_tab": "mcp_integrations",
            "title": "MCP Integrations",
            "new_object_url": reverse("mcp_integrations:new", args=[self.kwargs["team_slug"]]),
            "table_url": reverse("mcp_integrations:table", args=[self.kwargs["team_slug"]]),
        }


class McpServerTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):
    model = McpServer
    table_class = McpServerTable
    template_name = "table/single_table.html"
    permission_required = "mcp_integrations.view_mcpserver"

    def get_queryset(self):
        return McpServer.objects.filter(team=self.request.team)


class CreateMcpServer(LoginAndTeamRequiredMixin, PermissionRequiredMixin, CreateView):
    model = McpServer
    form_class = McpServerForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create MCP Server",
        "button_text": "Create",
        "active_tab": "mcp_integrations",
    }
    permission_required = "mcp_integrations.add_mcpserver"

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {
            "request": self.request,
        }

    def get_success_url(self):
        return reverse("single_team:manage_team", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        response = super().form_valid(form)
        sync_tools_task.delay(form.instance.id)
        messages.success(self.request, "MCP Server created successfully and tools sync has been queued.")
        return response


class EditMcpServer(LoginAndTeamRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = McpServer
    form_class = McpServerForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update MCP Server",
        "button_text": "Update",
        "active_tab": "mcp_integrations",
    }
    permission_required = "mcp_integrations.change_mcpserver"

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {
            "request": self.request,
        }

    def get_queryset(self):
        return McpServer.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("single_team:manage_team", args=[self.request.team.slug])


class DeleteMcpServer(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "mcp_integrations.delete_mcpserver"

    def delete(self, request, team_slug: str, pk: int):
        mcp_server = get_object_or_404(McpServer, id=pk, team=request.team)
        mcp_server.delete()
        messages.success(request, "MCP Server Deleted")
        return HttpResponse()


@login_required
@permission_required("mcp_integrations.change_mcpserver", raise_exception=True)
def trigger_refresh_view(request, team_slug: str, pk: int):
    mcp_server = get_object_or_404(McpServer, id=pk, team__slug=team_slug, team=request.team)
    sync_tools_task.delay(mcp_server.id)
    messages.success(request, "Tool refresh has been queued.")
    return redirect(reverse("single_team:manage_team", args=[team_slug]))
