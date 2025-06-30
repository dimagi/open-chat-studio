import logging
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, FormView, ListView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.documents import tasks
from apps.documents.forms import CollectionForm, CreateCollectionFromAssistantForm
from apps.documents.models import ChunkingStrategy, Collection, CollectionFile, CollectionFileMetadata, FileStatus
from apps.documents.tables import CollectionsTable
from apps.files.models import File, FileChunkEmbedding
from apps.generics import actions
from apps.generics.chips import Chip
from apps.generics.help import render_help_with_link
from apps.service_providers.models import LlmProviderTypes
from apps.service_providers.utils import get_embedding_provider_choices
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
            "button_style": "btn-primary",
            "actions": [
                actions.Action(
                    "documents:create_from_assistant",
                    label="Create from assistant",
                    icon_class="fa-solid fa-robot",
                    title="Create an indexed collection from an OpenAI assistant's file search tools",
                    required_permissions=["documents.add_collection"],
                )
            ],
        }


@login_and_team_required
@permission_required("documents.view_collection", raise_exception=True)
def single_collection_home(request, team_slug: str, pk: int):
    collection = get_object_or_404(Collection.objects.select_related("team"), id=pk, team__slug=team_slug)

    chunk_count_query = (
        FileChunkEmbedding.objects.filter(collection_id=OuterRef("collection_id"), file_id=OuterRef("file_id"))
        .values("collection_id", "file_id")
        .annotate(count=Count("id"))
        .values_list("count")
    )

    collection_files = CollectionFile.objects.filter(collection=collection).annotate(
        chunk_count=Subquery(chunk_count_query, output_field=IntegerField())
    )

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

    supported_extensions = (
        settings.SUPPORTED_FILE_TYPES["file_search"]
        if collection.is_index
        else settings.SUPPORTED_FILE_TYPES["collections"]
    )
    files = []
    invalid_files = []

    # Validate extensions
    for uploaded_file in request.FILES.getlist("files"):
        ext = Path(uploaded_file.name).suffix.lower()
        if not ext or ext not in supported_extensions:
            invalid_files.append(uploaded_file.name)
        else:
            files.append(uploaded_file)

    # All files are unsupported
    if not files:
        messages.error(request, _("All selected files are invalid. Unsupported: ") + ", ".join(invalid_files))
        return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)

    with transaction.atomic():
        # Create File objects
        created_files = []
        for uploaded_file in files:
            created_files.append(
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
            chunk_size = request.POST.get("chunk_size")
            chunk_overlap = request.POST.get("chunk_overlap")
            metadata = CollectionFileMetadata(
                chunking_strategy=ChunkingStrategy(
                    chunk_size=int(chunk_size) if chunk_size else 800,
                    chunk_overlap=int(chunk_overlap) if chunk_overlap else 400,
                )
            )

        collection_files = CollectionFile.objects.bulk_create(
            [
                CollectionFile(collection=collection, file=file, status=status, metadata=metadata)
                for file in created_files
            ]
        )

    if collection.is_index:
        tasks.index_collection_files_task.delay([cf.id for cf in collection_files])

    # Notify on UI about unsupported files
    if invalid_files:
        messages.warning(
            request, _("Some files were skipped because of unsupported extensions: ") + ", ".join(invalid_files)
        )

    messages.success(request, _(f"Added {len(created_files)} files to collection."))

    return redirect("documents:single_collection_home", team_slug=team_slug, pk=pk)


@require_POST
@login_and_team_required
@permission_required("documents.change_collection")
@transaction.atomic()
def delete_collection_file(request, team_slug: str, pk: int, file_id: int):
    collection_file = get_object_or_404(
        CollectionFile.objects.select_related("collection", "file"), collection_id=pk, file_id=file_id
    )

    file = collection_file.file
    collection = collection_file.collection
    collection_file.delete()

    if file.is_used():
        if collection.is_index:
            # Remove it from the index only
            index_manager = collection.get_index_manager()
            index_manager.delete_file_from_index(file_id=file.external_id)
    else:
        # Nothing else is using it
        if collection.is_index:
            index_manager = collection.get_index_manager()
            index_manager.delete_files(files=[file])

        collection_file.file.delete_or_archive()

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Create a mapping of provider ID to provider type for JavaScript
        provider_types = {}
        for provider in self.request.team.llmprovider_set.all():
            provider_types[provider.id] = provider.type

        context["provider_types"] = provider_types
        context["embedding_model_options"] = get_embedding_provider_choices(self.request.team)
        context["open_ai_provider_ids"] = list(
            self.request.team.llmprovider_set.filter(type=LlmProviderTypes.openai).values_list("id", flat=True)
        )
        return context


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
        collection = form.instance
        if form.instance.is_index:
            if form.cleaned_data["is_remote_index"]:
                collection.ensure_remote_index_created()

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

        if form.instance.is_index and form.instance.is_remote_index and "llm_provider" in form.changed_data:
            with transaction.atomic():
                collection.openai_vector_store_id = None  # Reset the vector store ID
                collection.ensure_remote_index_created()
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


class CreateCollectionFromAssistant(LoginAndTeamRequiredMixin, FormView, PermissionRequiredMixin):
    form_class = CreateCollectionFromAssistantForm
    template_name = "documents/create_from_assistant_form.html"
    permission_required = "documents.add_collection"
    extra_context = {
        "title": "Create Collection from Assistant",
        "button_text": "Create Collection",
        "active_tab": "collections",
    }
    object = None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_success_url(self):
        return reverse("documents:single_collection_home", args=[self.request.team.slug, self.object.id])

    def form_valid(self, form):
        with transaction.atomic():
            assistant = form.cleaned_data["assistant"]
            collection_name = form.cleaned_data["collection_name"]
            collection = Collection.objects.create(
                team=self.request.team,
                name=collection_name,
                is_index=True,
                is_remote_index=True,
                llm_provider=assistant.llm_provider,
            )
            self.object = collection
        tasks.create_collection_from_assistant_task.delay(
            collection_id=collection.id,
            assistant_id=assistant.id,
        )
        return HttpResponseRedirect(self.get_success_url())


class FileChunkEmbeddingListView(LoginAndTeamRequiredMixin, ListView, PermissionRequiredMixin):
    """View to display file chunks for a specific file in a collection with pagination"""

    model = FileChunkEmbedding
    template_name = "documents/file_chunks.html"
    context_object_name = "chunks"
    paginate_by = 10
    permission_required = ("documents.view_collection", "files.view_file")

    def get_queryset(self):
        collection_id = self.kwargs["collection_id"]
        file_id = self.kwargs["file_id"]

        # Get chunks for this file in this collection, ordered by chunk number
        return FileChunkEmbedding.objects.filter(
            collection_id=collection_id, file_id=file_id, team__slug=self.kwargs["team_slug"]
        ).order_by("chunk_number")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        team_slug = self.kwargs["team_slug"]
        collection_id = self.kwargs["collection_id"]
        file_id = self.kwargs["file_id"]

        collection_file = get_object_or_404(
            CollectionFile.objects.select_related("file", "collection"),
            collection__team__slug=team_slug,
            file_id=file_id,
            collection_id=collection_id,
        )

        chunking_strategy = collection_file.metadata.chunking_strategy

        context.update(
            {
                "chunk_size": chunking_strategy.chunk_size,
                "chunk_overlap": chunking_strategy.chunk_overlap,
                "collection": collection_file.collection,
                "file": collection_file.file,
            }
        )

        return context
