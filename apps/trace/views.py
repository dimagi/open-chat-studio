import logging
from collections import defaultdict

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Prefetch
from django.urls import reverse
from django.views.generic import DetailView, TemplateView
from django_tables2 import SingleTableView

from apps.annotations.models import CustomTaggedItem
from apps.service_providers.tracing.langfuse import get_langfuse_api_client
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.trace.filters import TraceFilter, get_trace_filter_context_data
from apps.trace.models import Trace, TraceStatus
from apps.trace.tables import TraceTable
from apps.web.dynamic_filters.datastructures import FilterParams

logger = logging.getLogger(__name__)


class TracesHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):  # ty: ignore[invalid-method-override]
        return {
            "active_tab": "traces",
            "title": "Traces",
            "page_title": "Traces",
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
        return (
            Trace.objects.select_related("experiment", "session", "participant", "input_message", "output_message")
            .prefetch_related(
                Prefetch(
                    "input_message__tagged_items",
                    queryset=CustomTaggedItem.objects.select_related("tag", "user"),
                    to_attr="prefetched_tagged_items",
                ),
                Prefetch(
                    "output_message__tagged_items",
                    queryset=CustomTaggedItem.objects.select_related("tag", "user"),
                    to_attr="prefetched_tagged_items",
                ),
            )
            .filter(team=self.request.team)
        )


class TraceLangfuseSpansView(LoginAndTeamRequiredMixin, DetailView, PermissionRequiredMixin):
    model = Trace
    template_name = "trace/partials/langfuse_spans.html"
    permission_required = "trace.view_trace"

    def get_queryset(self):
        return Trace.objects.select_related("experiment__trace_provider", "output_message").filter(
            team=self.request.team
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        trace = self.object
        langfuse_trace_id, langfuse_trace_url = self._get_langfuse_info(trace)
        context["langfuse_trace_url"] = langfuse_trace_url

        experiment = trace.experiment
        trace_provider = experiment.trace_provider if experiment else None
        if not langfuse_trace_id or not trace_provider:
            context["langfuse_available"] = False
            context["langfuse_error"] = False
            return context

        try:
            api_client = get_langfuse_api_client(trace_provider.config)
            langfuse_trace = api_client.trace.get(langfuse_trace_id)
            observations = langfuse_trace.observations or []
            context["langfuse_available"] = True
            context["langfuse_error"] = False
            root_observations = [o for o in observations if not o.parent_observation_id]
            child_map = self._build_child_map(observations)
            flattened = self._flatten_observations(root_observations, child_map)
            context["flattened_observations"] = flattened
            context["auto_selected_span_id"] = self._get_auto_selected_span_id(flattened)
        except Exception:
            logger.exception("Error fetching Langfuse trace %s", langfuse_trace_id)
            context["langfuse_available"] = False
            context["langfuse_error"] = True

        return context

    def _get_langfuse_info(self, trace) -> tuple[str | None, str | None]:
        if not trace.output_message:
            return None, None
        for info in trace.output_message.trace_info:
            if info.get("trace_provider") == "langfuse":
                return info.get("trace_id"), info.get("trace_url")
        return None, None

    def _build_child_map(self, observations) -> dict:
        child_map: dict = defaultdict(list)
        for obs in observations:
            if obs.parent_observation_id:
                child_map[obs.parent_observation_id].append(obs)
        return dict(child_map)

    def _flatten_observations(self, root_observations, child_map) -> list:
        """Return depth-first ordered flat list of {"observation": obs, "depth": int} dicts."""
        result = []

        def _walk(obs, depth):
            result.append({"observation": obs, "depth": depth})
            for child in child_map.get(obs.id, []):
                _walk(child, depth + 1)

        for root in root_observations:
            _walk(root, 0)
        return result

    def _get_auto_selected_span_id(self, flattened_observations) -> str | None:
        """Return the first ERROR span id; fall back to the first span."""
        for item in flattened_observations:
            if item["observation"].level == "ERROR":
                return item["observation"].id
        return flattened_observations[0]["observation"].id if flattened_observations else None
