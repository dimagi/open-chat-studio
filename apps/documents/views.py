import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.assistants.sync import delete_file_from_openai
from apps.documents import tasks
from apps.documents.forms import CollectionForm
from apps.documents.models import ChunkingStrategy, Collection, CollectionFile, CollectionFileMetadata, FileStatus
from apps.documents.tables import CollectionsTable
from apps.files.models import File
from apps.generics.chips import Chip
from apps.generics.help import render_help_with_link
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.search import similarity_search

logger = logging.getLogger("ocs.documents.views")


class CollectionHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    template_name = "generic/object_home.html"
    permission_required = "documents.view_collection"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "collections",
            "title": "Collections",
            "title_help_content": render_help_with_link("", "collections"),
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

    collection_files_count = collection_files.count()
    context = {
        "collection": collection,
        "collection_files": collection_files,
        "collection_files_count": collection_files_count,
        "collections_supported_file_types": settings.SUPPORTED_FILE_TYPES["collections"],
        "file_search_supported_file_types": settings.SUPPORTED_FILE_TYPES["file_search"],
        "max_summary_length": settings.MAX_SUMMARY_LENGTH,
        "max_files_per_collection": settings.MAX_FILES_PER_COLLECTION,
        "files_remaining": settings.MAX_FILES_PER_COLLECTION - collection_files_count,
        "max_file_size_mb": settings.MAX_FILE_SIZE_MB,
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
        status = FileStatus.PENDING if collection.is_index else ""
        metadata = None
        if collection.is_index:
            metadata = CollectionFileMetadata(
                chunking_strategy=ChunkingStrategy(
                    chunk_size=int(request.POST.get("chunk_size")), chunk_overlap=int(request.POST.get("chunk_overlap"))
                )
            )

        collection_files = CollectionFile.objects.bulk_create(
            [CollectionFile(collection=collection, file=file, status=status, metadata=metadata) for file in files]
        )

    if collection.is_index:
        tasks.index_collection_files_task.delay([cf.id for cf in collection_files])

    messages.success(request, f"Added {len(files)} files to collection")
    return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)


@require_POST
@login_and_team_required
@permission_required("documents.change_collection")
@transaction.atomic()
def delete_collection_file(request, team_slug: str, pk: int, file_id: int):
    collection_file = get_object_or_404(
        CollectionFile.objects.select_related("collection", "file"), collection_id=pk, file_id=file_id
    )

    # Check if file is also used by assistants before deletion
    file_used_by_assistants = _is_file_used_by_assistants(collection_file.file, request.team)
    file_used_in_other_collections = _is_file_used_in_other_collections(collection_file.file, request.team, pk)
    
    # Only delete/archive the file if it's not used elsewhere
    if not file_used_by_assistants and not file_used_in_other_collections:
        collection_file.file.delete_or_archive()
        if collection_file.collection.is_index:
            client = collection_file.collection.llm_provider.get_llm_service().get_raw_client()
            delete_file_from_openai(client, collection_file.file)
    elif collection_file.collection.is_index:
        # If file is used elsewhere, just remove it from this collection's vector store
        client = collection_file.collection.llm_provider.get_llm_service().get_raw_client()
        vector_store_manager = collection_file.collection.llm_provider.get_index_manager()
        if collection_file.file.external_id:
            vector_store_manager.delete_file(
                vector_store_id=collection_file.collection.openai_vector_store_id,
                file_id=collection_file.file.external_id
            )

    collection_file.delete()

    messages.success(request, "File removed from collection")
    return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)


class CollectionTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    model = Collection
    paginate_by = 25
    table_class = CollectionsTable
    template_name = "table/single_table.html"
    permission_required = "documents.view_collection"

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


class CreateCollection(LoginAndTeamRequiredMixin, CollectionFormMixin, CreateView, PermissionRequiredMixin):
    model = Collection
    form_class = CollectionForm
    template_name = "documents/collection_form.html"
    permission_required = "documents.add_collection"
    extra_context = {
        "title": "Create Collection",
        "button_text": "Create",
        "active_tab": "collections",
    }

    def get_success_url(self):
        return reverse("documents:single_collection_home", args=[self.request.team.slug, self.object.id])

    @transaction.atomic()
    def form_valid(self, form):
        form.instance.team = self.request.team
        response = super().form_valid(form)
        if form.instance.is_index:
            collection = form.instance
            manager = collection.llm_provider.get_index_manager()
            collection.openai_vector_store_id = manager.create_vector_store(name=collection.index_name)
            collection.save(update_fields=["openai_vector_store_id"])

        return response


class EditCollection(LoginAndTeamRequiredMixin, CollectionFormMixin, UpdateView, PermissionRequiredMixin):
    model = Collection
    form_class = CollectionForm
    template_name = "documents/collection_form.html"
    permission_required = "documents.change_collection"
    extra_context = {
        "title": "Update Collection",
        "button_text": "Update",
        "active_tab": "collections",
    }

    def get_queryset(self):
        return Collection.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("documents:single_collection_home", args=[self.request.team.slug, self.object.id])

    def form_valid(self, form):
        response = super().form_valid(form)

        collection = form.instance
        old_vector_store_id = collection.openai_vector_store_id

        if form.instance.is_index and "llm_provider" in form.changed_data:
            with transaction.atomic():
                new_manager = collection.llm_provider.get_index_manager()
                collection.openai_vector_store_id = new_manager.create_vector_store(collection.index_name)
                collection.save(update_fields=["openai_vector_store_id"])

            CollectionFile.objects.filter(collection_id=collection.id).update(status=FileStatus.PENDING)
            tasks.migrate_vector_stores.delay(
                collection_id=form.instance.id,
                from_vector_store_id=old_vector_store_id,
                from_llm_provider_id=form.initial["llm_provider"],
            )
        return response


class DeleteCollection(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "documents.delete_collection"

    def delete(self, request, team_slug: str, pk: int):
        collection = get_object_or_404(Collection, team__slug=team_slug, id=pk)

        if collection.archive():
            messages.success(request, "Collection deleted")
            return HttpResponse()
        else:
            # Find and show references.
            # For working versions, the Pipelines.
            # For versions, the experiments

            pipeline_node_chips = [
                Chip(
                    label=node.pipeline.name,
                    url=node.pipeline.get_absolute_url(),
                )
                for node in collection.get_related_nodes_queryset()
            ]
            experiment_chips = []
            for version in collection.versions.all():
                if experiments := version.get_related_experiments_queryset():
                    experiment_chips.extend(
                        [
                            Chip(
                                label=f"{experiment.name} {experiment.get_version_name()} [published]",
                                url=experiment.get_absolute_url(),
                            )
                            for experiment in experiments
                        ]
                    )

            response = render_to_string(
                "generic/referenced_objects.html",
                context={
                    "object_name": "collection",
                    "pipeline_nodes": pipeline_node_chips,
                    "experiments_with_pipeline_nodes": experiment_chips,
                },
            )
            return HttpResponse(response, headers={"HX-Reswap": "none"}, status=400)


@require_POST
@login_and_team_required
@permission_required("documents.change_collection", raise_exception=True)
def retry_failed_uploads(request, team_slug: str, pk: int):
    queryset = CollectionFile.objects.filter(collection_id=pk, status=FileStatus.FAILED)
    collection_file_ids = list(queryset.values_list("id", flat=True))
    queryset.update(status=FileStatus.PENDING)
    tasks.index_collection_files_task.delay(collection_file_ids)
    return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)


class CreateCollectionFromAssistant(LoginAndTeamRequiredMixin, CollectionFormMixin, CreateView, PermissionRequiredMixin):
    model = Collection
    form_class = CollectionForm
    template_name = "documents/collection_form.html"
    permission_required = "documents.add_collection"
    extra_context = {
        "title": "Create Collection from Assistant",
        "button_text": "Create",
        "active_tab": "collections",
    }

    def get_success_url(self):
        return reverse("documents:single_collection_home", args=[self.request.team.slug, self.object.id])

    @transaction.atomic()
    def form_valid(self, form):
        form.instance.team = self.request.team
        response = super().form_valid(form)
        
        # Move the logic to a background task
        tasks.create_collection_from_assistant_task.delay(
            collection_id=form.instance.id,
            assistant_id=self.kwargs.get('assistant_id'),
            team_id=self.request.team.id
        )
        
        return response


def _is_file_used_by_assistants(file, team):
    """Check if a file is used by any assistants in the team"""
    from apps.assistants.models import ToolResources
    return ToolResources.objects.filter(
        assistant__team=team,
        files=file,
        assistant__is_archived=False
    ).exists()


def _is_file_used_in_other_collections(file, team, current_collection_id):
    """Check if a file is used in other collections besides the current one"""
    return CollectionFile.objects.filter(
        file=file,
        collection__team=team,
        collection__is_archived=False
    ).exclude(collection_id=current_collection_id).exists()
