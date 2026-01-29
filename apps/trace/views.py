from collections import defaultdict

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Prefetch
from django.urls import reverse
from django.views.generic import DetailView, TemplateView
from django_tables2 import SingleTableView

from apps.annotations.models import CustomTaggedItem
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.trace.filters import TraceFilter, get_trace_filter_context_data
from apps.trace.models import Span, Trace, TraceStatus
from apps.trace.tables import TraceTable
from apps.web.dynamic_filters.datastructures import FilterParams


class TracesHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "traces",
            "title": "Traces",
            "table_url": reverse("trace:table", args=[team_slug]),
            **get_trace_filter_context_data(self.request.team),
        }


class TraceTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
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
        trace_filter = TraceFilter()
        return trace_filter.apply(queryset, filter_params=FilterParams.from_request(self.request), timezone=timezone)


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
        # Get ALL spans for this trace in one query. The recursive tree building will be done in Python to avoid N+1
        # queries.
        all_spans = Span.objects.filter(trace=self.object).prefetch_related(
            Prefetch(
                "tagged_items",
                queryset=CustomTaggedItem.objects.select_related("tag"),
                to_attr="prefetched_tagged_items",
            )
        )

        child_spans = defaultdict(list)
        for span in all_spans:
            if span.parent_span_id:
                child_spans[span.parent_span_id].append(span)

        root_spans = [span for span in all_spans if span.parent_span_id is None]
        context["root_spans"] = root_spans
        context["child_spans_map"] = child_spans
        return context
