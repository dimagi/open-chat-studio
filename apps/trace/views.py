from django.contrib.auth.mixins import PermissionRequiredMixin
from django.urls import reverse
from django.views.generic import TemplateView
from django_tables2 import SingleTableView

from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.trace.models import Trace
from apps.trace.tables import TraceTable


class TracesHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "traces",
            "title": "Traces",
            "table_url": reverse("traces:table", args=[team_slug]),
        }


class TraceTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    paginate_by = 25
    template_name = "table/single_table.html"
    model = Trace
    table_class = TraceTable
    permission_required = "trace.view_trace"

    def get_queryset(self):
        return (
            Trace.objects.select_related("participant", "experiment", "session")
            .filter(team__slug=self.request.team.slug)
            .order_by("-timestamp")
        )
