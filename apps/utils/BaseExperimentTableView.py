from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q
from django_tables2 import SingleTableView


class BaseExperimentTableView(SingleTableView, PermissionRequiredMixin):
    paginate_by = 25
    template_name = "table/single_table.html"

    def get_queryset(self):
        query_set = (
            self.model.objects.get_all()
            .filter(team=self.request.team, working_version__isnull=True)
            .order_by("is_archived")
        )
        show_archived = self.request.GET.get("show_archived") == "on"
        if not show_archived:
            query_set = query_set.filter(is_archived=False)

        search = self.request.GET.get("search")
        if search:
            name_similarity = TrigramSimilarity("name", search)
            description_similarity = TrigramSimilarity("description", search)
            query_set = (
                query_set.annotate(
                    similarity=name_similarity + description_similarity,
                )
                .filter(Q(similarity__gt=0.2) | Q(owner__username__icontains=search))
                .order_by("-similarity")
            )
        return query_set
