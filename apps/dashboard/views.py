import json

from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin

from .forms import DashboardFilterForm, SavedFilterForm
from .models import DashboardFilter
from .services import DashboardService


@method_decorator(login_and_team_required, name="dispatch")
class DashboardView(LoginAndTeamRequiredMixin, TemplateView):
    """Main dashboard view"""

    template_name = "dashboard/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Initialize filter form
        filter_form = DashboardFilterForm(data=self.request.GET if self.request.GET else None, team=self.request.team)

        # Get saved filters for this user/team
        saved_filters = DashboardFilter.objects.filter(team=self.request.team, user=self.request.user).order_by(
            "-is_default", "filter_name"
        )

        context.update(
            {
                "filter_form": filter_form,
                "saved_filters": saved_filters,
                "saved_filter_form": SavedFilterForm(),
                "active_tab": "dashboard",
                "page_title": "Dashboard",
            }
        )

        return context


@method_decorator(login_and_team_required, name="dispatch")
class DashboardApiView(LoginAndTeamRequiredMixin, TemplateView):
    """Base API view for dashboard data"""

    def get_dashboard_service(self):
        return DashboardService(self.request.team)

    def get_filter_params(self):
        """Extract filter parameters from request"""
        filter_form = DashboardFilterForm(data=self.request.GET, team=self.request.team)

        if filter_form.is_valid():
            return filter_form.get_filter_params()
        return {}

    def json_response(self, data):
        """Return JSON response with proper serialization"""
        return JsonResponse(data, encoder=DjangoJSONEncoder, safe=False)


class OverviewStatsApiView(DashboardApiView):
    """API endpoint for overview statistics"""

    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()

        stats = service.get_overview_stats(**filter_params)
        return self.json_response(stats)


class SessionAnalyticsApiView(DashboardApiView):
    """API endpoint for session analytics data (active sessions and active participants)"""

    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()
        granularity = request.GET.get("granularity", "daily")

        data = service.get_session_analytics_data(granularity=granularity, **filter_params)
        return self.json_response(data)


class MessageVolumeApiView(DashboardApiView):
    """API endpoint for message volume trends"""

    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()
        granularity = request.GET.get("granularity", "daily")

        data = service.get_message_volume_data(granularity=granularity, **filter_params)
        return self.json_response(data)


class BotPerformanceApiView(DashboardApiView):
    """API endpoint for bot performance summary"""

    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()

        # Get pagination parameters
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 10))
        order_by = request.GET.get("order_by", "messages")
        order_dir = request.GET.get("order_dir", "desc")

        data = service.get_bot_performance_summary(
            page=page, page_size=page_size, order_by=order_by, order_dir=order_dir, **filter_params
        )
        return self.json_response(data)


class UserEngagementApiView(DashboardApiView):
    """API endpoint for user engagement analysis"""

    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()
        limit = int(request.GET.get("limit", 10))

        data = service.get_user_engagement_data(limit=limit, **filter_params)
        return self.json_response(data)


class ChannelBreakdownApiView(DashboardApiView):
    """API endpoint for channel breakdown data"""

    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()

        data = service.get_channel_breakdown_data(**filter_params)
        return self.json_response(data)


class TagAnalyticsApiView(DashboardApiView):
    """API endpoint for tag analytics"""

    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()

        data = service.get_tag_analytics_data(**filter_params)
        return self.json_response(data)


class AverageResponseTimeApiView(DashboardApiView):
    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()
        granularity = request.GET.get("granularity", "daily")

        data = service.get_average_response_time_data(granularity=granularity, **filter_params)
        return self.json_response(data)


@method_decorator(login_and_team_required, name="dispatch")
class SaveFilterView(LoginAndTeamRequiredMixin, TemplateView):
    """Save filter presets"""

    def post(self, request, *args, **kwargs):
        form = SavedFilterForm(request.POST)

        if form.is_valid():
            # If marking as default, unmark other defaults
            if form.cleaned_data["is_default"]:
                DashboardFilter.objects.filter(team=request.team, user=request.user, is_default=True).update(
                    is_default=False
                )

            # Save filter
            filter_obj, created = DashboardFilter.objects.update_or_create(
                team=request.team,
                user=request.user,
                filter_name=form.cleaned_data["name"],
                defaults={
                    "filter_data": json.loads(form.cleaned_data["filter_data"]),
                    "is_default": form.cleaned_data["is_default"],
                },
            )

            return JsonResponse({"success": True, "message": "Filter saved successfully", "filter_id": filter_obj.id})

        return JsonResponse({"success": False, "errors": form.errors})


@method_decorator(login_and_team_required, name="dispatch")
class LoadFilterView(LoginAndTeamRequiredMixin, TemplateView):
    """Load saved filter preset"""

    def get(self, request, filter_id, *args, **kwargs):  # ty: ignore[invalid-method-override]
        try:
            filter_obj = DashboardFilter.objects.get(id=filter_id, team=request.team, user=request.user)

            return JsonResponse({"success": True, "filter_data": filter_obj.filter_data})

        except DashboardFilter.DoesNotExist:
            return JsonResponse({"success": False, "error": "Filter not found"})


@method_decorator(login_and_team_required, name="dispatch")
class DeleteFilterView(LoginAndTeamRequiredMixin, TemplateView):
    """Delete saved filter preset"""

    def delete(self, request, filter_id, *args, **kwargs):
        try:
            filter_obj = DashboardFilter.objects.get(id=filter_id, team=request.team, user=request.user)
            filter_obj.delete()

            return JsonResponse({"success": True, "message": "Filter deleted successfully"})

        except DashboardFilter.DoesNotExist:
            return JsonResponse({"success": False, "error": "Filter not found"})
