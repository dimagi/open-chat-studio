from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q
from django.urls import reverse
from django.views.generic import ListView, TemplateView

from apps.documents.models import Repository, RepositoryType
from apps.files.models import File
from apps.teams.mixins import LoginAndTeamRequiredMixin


class RepositoryHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "documents/repositories.html"

    def get_context_data(self, team_slug: str, tab_name: str, **kwargs):
        return {
            "active_tab": "manage_files",
            "title": "Manage Files",
            "tab_name": tab_name,
            "files_list_url": reverse("documents:files_list", kwargs={"team_slug": team_slug}),
            "collections_list_url": reverse("documents:collections_list", kwargs={"team_slug": team_slug}),
        }


class BaseObjectListView(ListView):
    details_url_name: str

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        context["details_url_name"] = self.details_url_name
        context["tab_name"] = self.tab_name
        return context


class BaseDetailsView(TemplateView):
    def get_context_data(self, team_slug: str, id, **kwargs):
        return {"object": self.model.objects.get(team__slug=team_slug, id=id)}


class FileListView(LoginAndTeamRequiredMixin, BaseObjectListView):
    template_name = "documents/list.html"
    model = File
    paginate_by = 10
    details_url_name = "documents:file_details"
    tab_name = "files"

    def get_queryset(self):
        queryset = super().get_queryset().filter(team__slug=self.kwargs["team_slug"]).order_by("-created_at")
        search = self.request.GET.get("search")
        if search:
            # TODO: Expand to search summary as well
            name_similarity = TrigramSimilarity("name", search)
            queryset = (
                queryset.annotate(similarity=name_similarity).filter(Q(similarity__gt=0.2)).order_by("-similarity")
            )
        return queryset


class FileDetails(LoginAndTeamRequiredMixin, BaseDetailsView):
    template_name = "documents/file_details.html"
    model = File


class CollectionListView(LoginAndTeamRequiredMixin, BaseObjectListView):
    template_name = "documents/list.html"
    model = Repository
    paginate_by = 10
    details_url_name = "documents:collection_details"
    tab_name = "collections"

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .filter(type=RepositoryType.COLLECTION, team__slug=self.kwargs["team_slug"])
            .order_by("-created_at")
        )

        search = self.request.GET.get("search")
        if search:
            name_similarity = TrigramSimilarity("name", search)
            queryset = (
                queryset.annotate(similarity=name_similarity).filter(Q(similarity__gt=0.2)).order_by("-similarity")
            )
        return queryset


class CollectionDetails(LoginAndTeamRequiredMixin, BaseDetailsView):
    template_name = "documents/collection_details.html"
    model = Repository
