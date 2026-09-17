from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin

from .engagement_forms import EngagementFilterForm
from .engagement_service import EngagementDashboardService


@method_decorator(login_and_team_required, name="dispatch")
class EngagementDashboardView(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "dashboard/engagement.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "filter_form": EngagementFilterForm(
                    data=self.request.GET if self.request.GET else None, team=self.request.team
                ),
                "active_tab": "engagement_dashboard",
                "page_title": "Engagement Dashboard",
            }
        )
        return context


@method_decorator(login_and_team_required, name="dispatch")
class EngagementApiView(LoginAndTeamRequiredMixin, TemplateView):
    def get_service(self) -> EngagementDashboardService:
        return EngagementDashboardService(self.request.team)

    def get_filter_params(self) -> dict:
        form = EngagementFilterForm(data=self.request.GET, team=self.request.team)
        return form.get_filter_params()

    def json_response(self, data):
        return JsonResponse(data, encoder=DjangoJSONEncoder, safe=False)


class EngagementSummaryApiView(EngagementApiView):
    def get(self, request, *args, **kwargs):
        data = self.get_service().get_engagement_summary_data(**self.get_filter_params())
        return self.json_response(data)


class EngagementFrequencyApiView(EngagementApiView):
    def get(self, request, *args, **kwargs):
        data = self.get_service().get_engagement_frequency_data(**self.get_filter_params())
        return self.json_response(data)


class NewVsReturningApiView(EngagementApiView):
    def get(self, request, *args, **kwargs):
        data = self.get_service().get_new_vs_returning_data(**self.get_filter_params())
        return self.json_response(data)


class AverageSessionDurationApiView(EngagementApiView):
    def get(self, request, *args, **kwargs):
        data = self.get_service().get_average_session_duration(**self.get_filter_params())
        return self.json_response(data)
