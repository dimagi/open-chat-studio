import json
import logging
from urllib.parse import quote

import httpx
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView
from waffle import flag_is_active

from apps.custom_actions.forms import CustomActionForm
from apps.custom_actions.models import CustomAction, HealthCheckStatus
from apps.custom_actions.tables import CustomActionTable
from apps.custom_actions.tasks import check_single_custom_action_health
from apps.teams.flags import Flags
from apps.teams.mixins import LoginAndTeamRequiredMixin

logger = logging.getLogger(__name__)


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

    def dispatch(self, request, *args, **kwargs):
        if not flag_is_active(request, Flags.TESTING_CUSTOM_ACTIONS.slug):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return CustomAction.objects.filter(team=self.request.team)

    def get_context_data(self, **kwargs):
        custom_action = get_object_or_404(CustomAction, id=self.kwargs["pk"], team=self.request.team)

        operations_data = {}
        for operation in custom_action.operations:
            path_param_values = {p.name: p.get_default_value() for p in operation.path_parameters}
            param_values = {
                p.name: p.get_default_value() for p in operation.query_parameters + operation.body_parameters
            }
            operations_data[operation.operation_id] = {
                "params": param_values,
                "pathParams": path_param_values,
            }

        return {
            "custom_action": custom_action,
            "operations": custom_action.operations,
            "operations_data": operations_data,
            "active_tab": "custom_actions",
        }


def _call_action_operation(server_url: str, operation, params: dict, path_params: dict, headers: dict) -> dict:
    """Make an HTTP request to a custom action endpoint.

    Returns a dict with keys: status_code, body, is_json.
    Raises ValueError for unsupported HTTP methods.
    Raises httpx.RequestError on network errors.
    """
    url_path = operation.path
    for param_name, param_value in path_params.items():
        url_path = url_path.replace(f"{{{param_name}}}", quote(str(param_value), safe=""))

    url = server_url.rstrip("/") + url_path
    method = operation.method.upper()
    request_kwargs = {"headers": headers, "timeout": 30.0}

    if method in ("GET", "DELETE"):
        request_kwargs["params"] = params
    elif method in ("POST", "PUT", "PATCH"):
        request_kwargs["json"] = params
    else:
        raise ValueError(f"Unsupported HTTP method: {operation.method}")

    response = getattr(httpx, method.lower())(url, **request_kwargs)

    try:
        body = response.json()
        is_json = True
    except (json.JSONDecodeError, ValueError):
        body = response.text
        is_json = False

    return {"status_code": response.status_code, "body": body, "is_json": is_json}


class TestCustomActionEndpoint(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    """Handle test requests for custom action endpoints."""

    permission_required = "custom_actions.view_customaction"

    def dispatch(self, request, *args, **kwargs):
        if not flag_is_active(request, Flags.TESTING_CUSTOM_ACTIONS.slug):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, team_slug: str, pk: int):
        custom_action = get_object_or_404(CustomAction, id=pk, team=request.team)

        try:
            body = json.loads(request.body)
            operation_id = body.get("operation_id")
            params = body.get("params", {})
            path_params = body.get("path_params", {})
        except (json.JSONDecodeError, TypeError):
            return JsonResponse({"error": "Invalid request body"}, status=400)

        operations_by_id = custom_action.get_operations_by_id()
        operation = operations_by_id.get(operation_id)
        if not operation:
            return JsonResponse({"error": f"Operation {operation_id} not found"}, status=404)

        try:
            auth_service = custom_action.get_auth_service()
            headers = auth_service.get_auth_headers() if auth_service else {}
            result = _call_action_operation(custom_action.server_url, operation, params, path_params, headers)

            is_health_endpoint = operation.path == custom_action.healthcheck_path
            server_was_down_previously = custom_action.health_status in [
                HealthCheckStatus.DOWN,
                HealthCheckStatus.UNKNOWN,
            ]
            server_is_healthy_now = 200 <= result["status_code"] < 300
            # Only update health status from down→up here; down transitions are managed
            # by the dedicated health check task so the tester doesn't clobber its state.
            if is_health_endpoint and server_was_down_previously and server_is_healthy_now:
                custom_action.health_status = HealthCheckStatus.UP
                custom_action.save(update_fields=["health_status"])

            return JsonResponse(result)

        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)
        except httpx.RequestError as e:
            return JsonResponse({"error": f"Request failed: {str(e)}"}, status=500)
        except Exception:
            logger.exception("Unexpected error testing endpoint pk=%s", pk)
            return JsonResponse({"error": "An unexpected error occurred"}, status=500)
