from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.documents.forms import CollectionForm
from apps.documents.models import Collection, CollectionFile, FileStatus
from apps.documents.tables import CollectionsTable
from apps.files.models import File
from apps.generics.chips import Chip
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.search import similarity_search


class CollectionHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "collections",
            "title": "Collections",
            # "title_help_content": render_help_with_link("", "survey"),
            "new_object_url": reverse("documents:collection_new", args=[team_slug]),
            "table_url": reverse("documents:collection_table", args=[team_slug]),
            "enable_search": True,
        }


@login_and_team_required
@permission_required("documents.view_collection", raise_exception=True)
def single_collection_home(request, team_slug: str, pk: int):
    collection = get_object_or_404(Collection.objects.select_related("team"), id=pk, team__slug=team_slug)

    collection_files = CollectionFile.objects.filter(collection=collection)
    # Load the labels for the file statuses

    context = {
        "collection": collection,
        "collection_files": collection_files,
        "supported_file_types": settings.MEDIA_SUPPORTED_FILE_TYPES,
        "max_summary_length": settings.MAX_SUMMARY_LENGTH,
    }
    return render(request, "documents/single_collection_home.html", context)


@require_POST
@login_and_team_required
@permission_required("documents.change_collection")
def add_collection_files(request, team_slug: str, pk: int):
    collection = get_object_or_404(Collection, id=pk, team__slug=team_slug)
    # Create files
    with transaction.atomic():
        files = []
        for uploaded_file in request.FILES.getlist("files"):
            files.append(
                File.objects.create(
                    team=request.team,
                    name=uploaded_file.name,
                    file=uploaded_file,
                    summary=request.POST[uploaded_file.name] if not collection.is_index else "",
                )
            )

        # Create file links
        status = FileStatus.IN_PROGRESS if collection.is_index else ""
        metadata = {}
        if collection.is_index:
            metadata["chunking_strategy"] = {
                "size": request.POST.get("chunk_size"),
                "overlap": request.POST.get("chunk_overlap"),
            }

        CollectionFile.objects.bulk_create(
            [CollectionFile(collection=collection, file=file, status=status, metadata=metadata) for file in files]
        )
        # TODO: Call task to upload files to OpenAI

    messages.success(request, f"Added {len(files)} files to collection")
    return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)


@login_and_team_required
@permission_required("documents.change_collection")
@require_POST
@transaction.atomic()
def delete_collection_file(request, team_slug: str, pk: int, file_id: int):
    collection_file = get_object_or_404(CollectionFile, collection_id=pk, file_id=file_id)
    collection_file.file.delete_or_archive()
    collection_file.delete()
    messages.success(request, "File removed from collection")
    return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)


class CollectionTableView(LoginAndTeamRequiredMixin, SingleTableView):
    model = Collection
    paginate_by = 25
    table_class = CollectionsTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        queryset = Collection.objects.filter(team=self.request.team, is_version=False).order_by("-created_at")
        if search := self.request.GET.get("search"):
            queryset = similarity_search(queryset, search_phase=search, columns=["name"])
        return queryset


class CollectionFormMixin:
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs


class CreateCollection(LoginAndTeamRequiredMixin, CollectionFormMixin, CreateView):
    model = Collection
    form_class = CollectionForm
    template_name = "documents/collection_form.html"
    extra_context = {
        "title": "Create Collection",
        "button_text": "Create",
        "active_tab": "collections",
    }

    def get_success_url(self):
        return reverse("documents:single_collection_home", args=[self.request.team.slug, self.object.id])

    def form_valid(self, form):
        form.instance.team = self.request.team
        return super().form_valid(form)

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.success(request, "Collection Created")
        return response


class EditCollection(LoginAndTeamRequiredMixin, CollectionFormMixin, UpdateView):
    model = Collection
    form_class = CollectionForm
    template_name = "documents/collection_form.html"
    extra_context = {
        "title": "Update Collection",
        "button_text": "Update",
        "active_tab": "collections",
    }

    def get_queryset(self):
        return Collection.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("documents:collection_home", args=[self.request.team.slug])


class DeleteCollection(LoginAndTeamRequiredMixin, View):
    def delete(self, request, team_slug: str, pk: int):
        collection = get_object_or_404(Collection, team__slug=team_slug, id=pk)
        if nodes := collection.get_node_references():
            response = render_to_string(
                "assistants/partials/referenced_objects.html",
                context={
                    "object_name": "collection",
                    "pipeline_nodes": [
                        Chip(label=node.pipeline.name, url=node.pipeline.get_absolute_url()) for node in nodes.all()
                    ],
                },
            )
            return HttpResponse(response, headers={"HX-Reswap": "none"}, status=400)
        else:
            collection.archive()
            messages.success(request, "Collection deleted")
            return HttpResponse()
