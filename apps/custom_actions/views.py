import json

import httpx
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.custom_actions.forms import CustomActionForm
from apps.custom_actions.models import CustomAction, HealthCheckStatus
from apps.custom_actions.tables import CustomActionTable
from apps.custom_actions.tasks import check_single_custom_action_health
from apps.teams.mixins import LoginAndTeamRequiredMixin


class CustomActionHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        return {
            "active_tab": "custom_actions",
            "title": "Custom Actions",
            "new_object_url": reverse("custom_actions:new", args=[self.kwargs["team_slug"]]),
            "table_url": reverse("custom_actions:table", args=[self.kwargs["team_slug"]]),
        }


class CustomActionTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):
    model = CustomAction
    table_class = CustomActionTable
    template_name = "table/single_table.html"
    permission_required = "custom_actions.view_customaction"

    def get_queryset(self):
        return CustomAction.objects.filter(team=self.request.team)


class CreateCustomAction(LoginAndTeamRequiredMixin, PermissionRequiredMixin, CreateView):
    model = CustomAction
    form_class = CustomActionForm
    template_name = "custom_actions/custom_actions_form.html"
    extra_context = {
        "title": "Create Custom Action",
        "button_text": "Create",
        "active_tab": "custom_actions",
    }
    permission_required = "custom_actions.add_customaction"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "request": self.request}

    def get_success_url(self):
        return reverse("single_team:manage_team", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        resp = super().form_valid(form)
        self.object.allowed_operations = list(self.object.get_operations_by_id())
        self.object.save(update_fields=["allowed_operations"])
        check_single_custom_action_health(self.object.id)
        return resp


class EditCustomAction(LoginAndTeamRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = CustomAction
    form_class = CustomActionForm
    template_name = "custom_actions/custom_actions_form.html"
    extra_context = {
        "title": "Update Custom Action",
        "button_text": "Update",
        "active_tab": "custom_actions",
    }
    permission_required = "custom_actions.change_customaction"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "request": self.request}

    def get_queryset(self):
        return CustomAction.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("single_team:manage_team", args=[self.request.team.slug])


class DeleteCustomAction(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "custom_actions.delete_customaction"

    def delete(self, request, team_slug: str, pk: int):
        consent_form = get_object_or_404(CustomAction, id=pk, team=request.team)
        consent_form.delete()
        messages.success(request, "Custom Action Deleted")
        return HttpResponse()


class CheckCustomActionHealth(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "custom_actions.view_customaction"

    def post(self, request, team_slug: str, pk: int):
        """Trigger an immediate health check for a custom action."""
        action = get_object_or_404(CustomAction, id=pk, team=request.team)

        if action.healthcheck_path:
            check_single_custom_action_health(action.id)
            action.refresh_from_db()
        else:
            messages.warning(request, "No health check path configured for this custom action.")

        return render(
            request,
            "custom_actions/health_status_column.html",
            {"team_slug": team_slug, "record": action},
        )


class CustomActionEndpointTester(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    """View for testing custom action endpoints."""

    template_name = "custom_actions/endpoint_tester.html"
    permission_required = "custom_actions.view_customaction"

    def get_queryset(self):
        return CustomAction.objects.filter(team=self.request.team)

    def get_context_data(self, **kwargs):
        custom_action = get_object_or_404(CustomAction, id=self.kwargs["pk"], team=self.request.team)

        operations_data = {}
        for operation in custom_action.operations:
            param_values = {}
            for param in operation.parameters:
                # Use the explicit default if set, otherwise provide sensible defaults based on schema type
                if param.default is not None:
                    param_values[param.name] = param.default
                else:
                    # Provide sensible defaults based on parameter type
                    if param.schema_type == "boolean":
                        param_values[param.name] = False
                    elif param.schema_type == "integer":
                        param_values[param.name] = 0
                    elif param.schema_type == "number":
                        param_values[param.name] = 0.0
                    elif param.schema_type == "array":
                        param_values[param.name] = []
                    else:  # string, object, and others
                        param_values[param.name] = ""
            operations_data[operation.operation_id] = param_values

        return {
            "custom_action": custom_action,
            "operations": custom_action.operations,
            "operations_data": operations_data,
            "active_tab": "custom_actions",
        }


class TestCustomActionEndpoint(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    """Handle test requests for custom action endpoints."""

    permission_required = "custom_actions.view_customaction"

    def post(self, request, team_slug: str, pk: int):
        custom_action = get_object_or_404(CustomAction, id=pk, team=request.team)

        try:
            body = json.loads(request.body)
            operation_id = body.get("operation_id")
            params = body.get("params", {})
        except (json.JSONDecodeError, TypeError):
            return JsonResponse(
                {"error": "Invalid request body"},
                status=400,
            )

        # Get the operation details
        operations_by_id = custom_action.get_operations_by_id()
        operation = operations_by_id.get(operation_id)

        if not operation:
            return JsonResponse(
                {"error": f"Operation {operation_id} not found"},
                status=404,
            )

        try:
            url = custom_action.server_url.rstrip("/") + operation.path

            auth_service = custom_action.get_auth_service()
            headers = {}
            if auth_service:
                auth_headers = auth_service.get_auth_headers()
                headers.update(auth_headers)

            # Make the request
            method = operation.method.upper()
            request_kwargs = {"headers": headers, "timeout": 30.0}

            # GET and DELETE use params; other methods use json body
            if method in ("GET", "DELETE"):
                request_kwargs["params"] = params
            elif method in ("POST", "PUT", "PATCH"):
                request_kwargs["json"] = params
            else:
                return JsonResponse(
                    {"error": f"Unsupported HTTP method: {operation.method}"},
                    status=400,
                )

            response = getattr(httpx, method.lower())(url, **request_kwargs)

            try:
                response_json = response.json()
                is_json = True
            except (json.JSONDecodeError, ValueError):
                response_json = response.text
                is_json = False

                # Update health status if this was a health endpoint and server was down but is healthy now
            is_health_endpoint = operation.path == custom_action.healthcheck_path
            server_was_down_previously = custom_action.health_status == HealthCheckStatus.DOWN
            server_is_healthy_now = response.status_code >= 200 and response.status_code < 300
            if is_health_endpoint and server_was_down_previously and server_is_healthy_now:
                custom_action.health_status = HealthCheckStatus.UP
                custom_action.save(update_fields=["health_status"])

            return JsonResponse(
                {
                    "status_code": response.status_code,
                    "body": response_json,
                    "is_json": is_json,
                }
            )

        except httpx.RequestError as e:
            return JsonResponse(
                {"error": f"Request failed: {str(e)}"},
                status=500,
            )
        except Exception as e:
            return JsonResponse(
                {"error": f"Error testing endpoint: {str(e)}"},
                status=500,
            )
