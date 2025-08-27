from django.contrib.auth.mixins import PermissionRequiredMixin
from django.urls import reverse
from django.views.generic import DetailView, TemplateView
from django_tables2 import SingleTableView

from apps.channels.models import ChannelPlatform
from apps.experiments.filters import DATE_RANGE_OPTIONS, FIELD_TYPE_FILTERS
from apps.experiments.models import Experiment
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.trace.filters import DynamicTraceFilter
from apps.trace.models import Span, Trace, TraceStatus
from apps.trace.tables import TraceTable


class TracesHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        span_tags = list(
            self.request.team.span_set.filter(tags__is_system_tag=True)
            .distinct("tags__name")
            .values_list("tags__name", flat=True)
        )

        experiments = (
            Experiment.objects.working_versions_queryset()
            .filter(team=self.request.team)
            .values("id", "name")
            .order_by("name")
        )
        experiment_list = [{"id": exp["id"], "label": exp["name"]} for exp in experiments]
        return {
            "active_tab": "traces",
            "title": "Traces",
            "table_url": reverse("trace:table", args=[team_slug]),
            "use_dynamic_filters": True,
            "df_filter_data_source_url": reverse("trace:table", args=[team_slug]),
            "df_filter_data_source_container_id": "data-table",
            "df_field_type_filters": FIELD_TYPE_FILTERS,
            "df_date_range_options": DATE_RANGE_OPTIONS,
            "df_channel_list": ChannelPlatform.for_filter(self.request.team),
            "df_available_tags": span_tags,
            "df_filter_columns": DynamicTraceFilter.columns,
            "df_date_range_column_name": "timestamp",
            "df_span_names": list(self.request.team.span_set.values_list("name", flat=True).distinct()),
            "df_experiment_list": experiment_list,
            "df_state_list": TraceStatus.values,
        }


class TraceTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    paginate_by = 25
    template_name = "table/single_table.html"
    model = Trace
    table_class = TraceTable
    permission_required = "trace.view_trace"

    def get_queryset(self):
        queryset = (
            Trace.objects.select_related("participant", "experiment", "session")
            .filter(team__slug=self.request.team.slug)
            .exclude(status=TraceStatus.PENDING)
            .order_by("-timestamp")
        )

        timezone = self.request.session.get("detected_tz", None)
        trace_filter = DynamicTraceFilter(queryset, self.request.GET, timezone)
        return trace_filter.apply()


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
