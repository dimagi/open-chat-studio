from django.contrib.auth.mixins import PermissionRequiredMixin
from django.urls import reverse
from django.views.generic import DetailView, TemplateView
from django_tables2 import SingleTableView

from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.trace.models import Span, Trace, TraceStatus
from apps.trace.tables import TraceTable


class TracesHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "traces",
            "title": "Traces",
            "table_url": reverse("trace:table", args=[team_slug]),
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
            .exclude(status=TraceStatus.PENDING)
            .order_by("-timestamp")
        )


class TraceDetailView(LoginAndTeamRequiredMixin, DetailView, PermissionRequiredMixin):
    model = Trace
    template_name = "trace/trace_detail.html"
    permission_required = "trace.view_trace"

    def get_queryset(self):
        return Trace.objects.select_related(
            "experiment", "session", "participant", "input_message", "output_message"
        ).filter(team=self.request.team)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["spans"] = Span.objects.filter(trace=self.object, parent_span_id__isnull=True)
        return context
