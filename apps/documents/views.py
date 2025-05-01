from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.db import models
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.documents.forms import CollectionForm
from apps.documents.models import Collection, CollectionFile, FileStatus
from apps.documents.tables import CollectionsTable
from apps.files.models import File
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin


class CollectionHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "manage_files",
            "title": "Collections",
            # "title_help_content": render_help_with_link("", "survey"),
            "new_object_url": reverse("documents:collection_new", args=[team_slug]),
            "table_url": reverse("documents:collection_table", args=[team_slug]),
        }


@login_and_team_required
@permission_required("documents.view_collection", raise_exception=True)
def single_collection_home(request, team_slug: str, pk: int):
    collection = get_object_or_404(Collection.objects.select_related("team"), id=pk, team__slug=team_slug)

    available_files = File.objects.filter(team__slug=team_slug, is_version=False).annotate(
        is_linked=models.Exists(CollectionFile.objects.filter(collection=collection, file=models.OuterRef("pk"))),
        file_status=models.Subquery(
            CollectionFile.objects.filter(collection=collection, file=models.OuterRef("pk")).values("status")[:1]
        ),
    )

    context = {
        "collection": collection,
        "collection_files": available_files.filter(is_linked=True),
        "supported_file_types": settings.MEDIA_SUPPORTED_FILE_TYPES,
        "max_summary_length": settings.MAX_SUMMARY_LENGTH,
        "available_files": available_files.filter(is_linked=False),
    }
    return render(request, "documents/single_collection_home.html", context)


@login_and_team_required
@permission_required("documents.change_collection")
@require_POST
def add_collection_files(request, team_slug: str, pk: int):
    collection = get_object_or_404(Collection, id=pk, team__slug=team_slug)
    file_ids = request.POST.getlist("files")
    files = collection.team.file_set.filter(id__in=file_ids, is_version=False)
    status = FileStatus.IN_PROGRESS if collection.is_index else ""

    CollectionFile.objects.bulk_create(
        [CollectionFile(collection=collection, file=file, status=status) for file in files]
    )

    messages.success(request, f"Added {len(files)} files to collection")
    return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)


@login_and_team_required
@permission_required("documents.change_collection")
@require_POST
def delete_collection_file(request, team_slug: str, pk: int, file_id: int):
    collection = get_object_or_404(Collection, id=pk, team__slug=team_slug)
    CollectionFile.objects.filter(collection=collection, file_id=file_id).delete()
    messages.success(request, "File removed from collection")
    return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)


class CollectionTableView(SingleTableView):
    model = Collection
    paginate_by = 25
    table_class = CollectionsTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return Collection.objects.filter(team=self.request.team, is_version=False)


class CollectionFormMixin:
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs


# TODO: Add loginrequired and permission_required for other views as well
class CreateCollection(CollectionFormMixin, CreateView):
    model = Collection
    form_class = CollectionForm
    template_name = "documents/collection_form.html"
    extra_context = {
        "title": "Create Collection",
        "button_text": "Create",
        "active_tab": "manage_files",
    }

    def get_success_url(self):
        return reverse("documents:collection_edit", args=[self.request.team.slug, self.object.id])

    def form_valid(self, form):
        form.instance.team = self.request.team
        return super().form_valid(form)

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.success(request, "Collection Created")
        return response


class EditCollection(CollectionFormMixin, UpdateView):
    model = Collection
    form_class = CollectionForm
    template_name = "documents/collection_form.html"
    extra_context = {
        "title": "Update Collection",
        "button_text": "Update",
        "active_tab": "manage_files",
    }

    def get_queryset(self):
        return Collection.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("documents:collection_home", args=[self.request.team.slug])


class DeleteCollection(LoginAndTeamRequiredMixin, View):
    def delete(self, request, team_slug: str, pk: int):
        collection = get_object_or_404(Collection, id=pk, team=request.team)
        collection.archive()
        messages.success(request, "Collection Deleted")
        return HttpResponse()
