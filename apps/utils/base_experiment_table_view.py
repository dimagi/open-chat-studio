from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Q
from django_tables2 import SingleTableView
from waffle import flag_is_active

from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.search import similarity_search


class BaseExperimentTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    template_name = "table/single_table.html"

    def get_table(self, **kwargs):
        table = super().get_table(**kwargs)
        if not flag_is_active(self.request, "flag_tracing"):
            table.exclude = ("trends",)
        return table

    def get_queryset(self):
        is_experiment = self.kwargs.get("is_experiment", False)
        query_set = (
            self.model.objects.get_all()
            .filter(team=self.request.team, working_version__isnull=True)
            .order_by("is_archived", "name")
        )
        if is_experiment:
            query_set = query_set.filter(pipeline__isnull=True)
        show_archived = self.request.GET.get("show_archived") == "on"
        if not show_archived:
            query_set = query_set.filter(is_archived=False)

        search = self.request.GET.get("search")
        if search:
            query_set = similarity_search(
                query_set,
                search_phase=search,
                columns=["name", "description"],
                extra_conditions=Q(owner__username__icontains=search),
                score=0.1,
            )
        return query_set
