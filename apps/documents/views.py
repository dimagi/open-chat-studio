import logging
from functools import cached_property
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Case, CharField, Count, Func, IntegerField, OuterRef, Subquery, Value, When
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_http_methods, require_POST
from django.views.generic import CreateView, FormView, ListView, TemplateView, UpdateView
from django_htmx.http import HttpResponseClientRedirect, reswap
from django_tables2 import SingleTableView

from apps.documents import tasks
from apps.documents.datamodels import ChunkingStrategy, CollectionFileMetadata
from apps.documents.forms import (
    CollectionForm,
    ConfluenceDocumentSourceForm,
    CreateCollectionFromAssistantForm,
    DocumentSourceForm,
    GithubDocumentSourceForm,
)
from apps.documents.models import (
    Collection,
    CollectionFile,
    DocumentSource,
    FileStatus,
    SourceType,
)
from apps.documents.tables import CollectionsTable
from apps.documents.tasks import sync_document_source_task
from apps.documents.utils import delete_collection_file
from apps.files.models import File, FileChunkEmbedding
from apps.generics import actions
from apps.generics.chips import Chip
from apps.generics.help import render_help_with_link
from apps.service_providers.models import LlmProviderTypes
from apps.service_providers.utils import get_embedding_provider_choices
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.search import similarity_search
from apps.web.waf import WafRule, waf_allow

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

    document_sources = DocumentSource.objects.working_versions_queryset().filter(collection=collection)
    collection_files_count = CollectionFile.objects.filter(collection=collection).count()
    context = {
        "collection": collection,
        "collection_files_count": collection_files_count,
        "document_sources": document_sources,
        "collections_supported_file_types": settings.SUPPORTED_FILE_TYPES["collections"],
        "file_search_supported_file_types": settings.SUPPORTED_FILE_TYPES["file_search"],
        "max_summary_length": settings.MAX_SUMMARY_LENGTH,
        "max_files_per_collection": settings.MAX_FILES_PER_COLLECTION,
        "max_files": settings.MAX_FILES_PER_COLLECTION,
        "max_file_size_mb": settings.MAX_FILE_SIZE_MB,
        "document_source_types": list(SourceType),
        "read_only": collection.is_a_version,
    }
    return render(request, "documents/single_collection_home.html", context)


@login_and_team_required
def collection_files_view(request, team_slug: str, collection_id: int, document_source_id: int = None):
    collection = get_object_or_404(Collection, id=collection_id, team__slug=team_slug)
    document_source = None
    if document_source_id:
        document_source = get_object_or_404(DocumentSource, id=document_source_id, team__slug=team_slug)
    chunk_count_query = (
        FileChunkEmbedding.objects.filter(collection_id=OuterRef("collection_id"), file_id=OuterRef("file_id"))
        .values("collection_id", "file_id")
        .annotate(count=Count("id"))
        .values_list("count")
    )
    search_query = request.GET.get("search", "").strip()
    collection_files = CollectionFile.objects.filter(collection=collection, document_source=document_source)
    if search_query:
        collection_files = collection_files.filter(file__name__icontains=search_query)
    collection_files = collection_files.annotate(
        chunk_count=Subquery(chunk_count_query, output_field=IntegerField()),
        directory=Case(
            When(
                file__name__contains="/",
                then=Func("file__name", Value("/[^/]*$"), Value("/"), function="regexp_replace"),
            ),
            default=Value(""),
            output_field=CharField(),
        ),
        depth=Func("file__name", Value("/"), function="regexp_count"),
    ).order_by("directory", "depth", "file__name")

    page = request.GET.get("page", 1)
    paginator = Paginator(collection_files, 10)
    try:
        paginated_collection_files = paginator.page(page)
    except PageNotAnInteger:
        paginated_collection_files = paginator.page(1)
    except EmptyPage:
        paginated_collection_files = paginator.page(paginator.num_pages)
    context = {
        "collection": collection,
        "collection_files": paginated_collection_files,
        "document_source": document_source,
        "allow_delete": document_source_id is None,
        "read_only": collection.is_a_version,
    }
    return render(request, "documents/partials/collection_files.html", context)


class QueryView(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    template_name = "documents/collection_query_view.html"
    permission_required = "documents.view_collection"

    def get_context_data(
        self,
        team_slug: str,
        pk: str,
    ):
        return {
            "active_tab": "collections",
            "title": "Query Collection",
            "collection": Collection.objects.get(id=pk, team__slug=team_slug),
        }


@login_and_team_required
@permission_required("documents.view_collection", raise_exception=True)
def query_collection(request, team_slug: str, pk: int):
    collection = get_object_or_404(Collection.objects.select_related("team"), id=pk, team__slug=team_slug)
    index_manager = collection.get_index_manager()
    context = {
        "chunks": index_manager.query(
            index_id=pk, query=request.GET.get("query"), top_k=int(request.GET.get("top_k", 5))
        ),
    }
    return render(request, "documents/collection_query_results.html", context)


class BaseDocumentSourceView(LoginAndTeamRequiredMixin, PermissionRequiredMixin):
    template_name = "documents/document_source_form_dialog.html"
    model = DocumentSource
    form_class = DocumentSourceForm

    @property
    def collection_id(self):
        return self.kwargs["collection_id"]

    @property
    def team_slug(self):
        return self.kwargs["team_slug"]

    @cached_property
    def collection(self):
        return get_object_or_404(
            Collection.objects.select_related("team"), id=self.collection_id, team__slug=self.team_slug
        )

    def get_form_class(self):
        return {
            SourceType.GITHUB: GithubDocumentSourceForm,
            SourceType.CONFLUENCE: ConfluenceDocumentSourceForm,
        }.get(self.source_type, DocumentSourceForm)

    @property
    def source_type(self):
        raise NotImplementedError

    def dispatch(self, request, *args, **kwargs):
        if not self.collection.is_index:
            messages.error(request, "Document sources can only be configured for indexed collections.")
            return redirect("documents:single_collection_home", team_slug=self.team_slug, pk=self.collection_id)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "collection": self.collection}

    def get_success_url(self):
        return reverse(
            "documents:single_collection_home", kwargs={"team_slug": self.team_slug, "pk": self.collection_id}
        )

    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            "collection": self.collection,
            "source_type": SourceType(self.source_type),
        }

    def form_valid(self, form):
        self.object = form.save()
        task = sync_document_source_task.delay(self.object.id)
        self.object.sync_task_id = task.task_id
        self.object.save(update_fields=["sync_task_id"])
        return HttpResponseClientRedirect(self.get_success_url())


class CreateDocumentSource(BaseDocumentSourceView, CreateView):
    permission_required = "documents.add_documentsource"

    @property
    def source_type(self):
        request_data = self.request.GET if self.request.method == "GET" else self.request.POST
        return request_data.get("source_type")

    def get_initial(self):
        return {
            "source_type": self.source_type,
        }


class EditDocumentSource(BaseDocumentSourceView, UpdateView):
    permission_required = "documents.change_documentsource"

    def get_queryset(self):
        return DocumentSource.objects.filter(team=self.request.team)

    @property
    def source_type(self):
        return self.object.source_type


@require_http_methods(["DELETE"])
@login_and_team_required
@permission_required("documents.change_collection")
def delete_document_source(request, team_slug: str, collection_id: int, pk: int):
    document_source = get_object_or_404(DocumentSource, id=pk, collection_id=collection_id, team__slug=team_slug)
    document_source.archive()
    return HttpResponse()


@require_POST
@login_and_team_required
@permission_required("documents.change_collection")
def sync_document_source(request, team_slug: str, collection_id: int, pk: int):
    """Trigger manual sync of a document source"""
    document_source = get_object_or_404(DocumentSource, id=pk, collection_id=collection_id, team__slug=team_slug)

    if document_source.sync_task_id:
        messages.warning(request, "This document source is already syncing.")
        return HttpResponse()

    task = sync_document_source_task.delay(document_source.id)
    document_source.sync_task_id = task.task_id
    document_source.save(update_fields=["sync_task_id"])
    messages.success(request, "Document source sync has been queued. This may take a few minutes.")
    return render(
        request,
        "documents/partials/document_source.html",
        context={
            "collection": document_source.collection,
            "team": request.team,
            "document_source": document_source,
        },
    )


@waf_allow(WafRule.SizeRestrictions_BODY)
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
def delete_collection_file_view(request, team_slug: str, pk: int, file_id: int):
    collection_file = get_object_or_404(
        CollectionFile.objects.select_related("collection", "file"), collection_id=pk, file_id=file_id
    )
    delete_collection_file(collection_file)
    messages.success(request, "File removed from collection")
    return HttpResponse()


@login_and_team_required
@permission_required("documents.view_collection", raise_exception=True)
def get_collection_file_status(request, team_slug: str, collection_id: int, pk: int):
    chunk_count_query = (
        FileChunkEmbedding.objects.filter(collection_id=OuterRef("collection_id"), file_id=OuterRef("file_id"))
        .values("collection_id", "file_id")
        .annotate(count=Count("id"))
        .values_list("count")
    )

    collection_file = get_object_or_404(
        CollectionFile.objects.annotate(
            chunk_count=Subquery(chunk_count_query, output_field=IntegerField())
        ).select_related("collection"),
        collection_id=collection_id,
        id=pk,
        collection__team__slug=team_slug,
    )

    return render(
        request,
        "documents/collection_file_status_response.html",
        {
            "collection_file": collection_file,
            "collection": collection_file.collection,
            "team": request.team,
        },
    )


@require_POST
@login_and_team_required
@permission_required("documents.view_collection", raise_exception=True)
def download_collection_files(request, team_slug: str, pk: int):
    """
    Start a background task to create a ZIP of all manually uploaded files.
    Returns HTML snippet with task_id for progress tracking.
    """
    collection = get_object_or_404(Collection, id=pk, team__slug=team_slug)
    manually_uploaded_count = CollectionFile.objects.filter(collection=collection, document_source__isnull=True).count()

    if manually_uploaded_count == 0:
        messages.error(request, "No manually uploaded files to download.")
        return HttpResponse(status=204)  # No content, will trigger htmx to do nothing

    # Start the task
    task = tasks.create_collection_zip_task.delay(collection.id, request.team.id)

    context = {
        "task_id": task.task_id,
        "collection": collection,
        "team": request.team,
        "manually_uploaded_files_count": manually_uploaded_count,
    }

    return render(request, "documents/partials/download_progress.html", context)


class CollectionTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    model = Collection
    table_class = CollectionsTable
    template_name = "table/single_table.html"
    permission_required = "documents.view_collection"

    def get_queryset(self):
        queryset = Collection.objects.filter(team=self.request.team, is_version=False).order_by("-created_at")
        if search := self.request.GET.get("search"):
            queryset = similarity_search(queryset, search_phase=search, columns=["name"])

        queryset = queryset.annotate(file_count=Count("files"))
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
            return reswap(HttpResponse(response, status=400), "none")


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
        "title_help_content": render_help_with_link("", "migrate_from_assistant"),
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
