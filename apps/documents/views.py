import logging

import openai
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

from apps.assistants.sync import OpenAiSyncError, VectorStoreManager, delete_file_from_openai
from apps.documents.forms import CollectionForm
from apps.documents.models import Collection, CollectionFile, FileStatus
from apps.documents.tables import CollectionsTable
from apps.documents.tasks import upload_files_to_vector_store_task
from apps.files.models import File
from apps.generics.chips import Chip
from apps.service_providers.models import LlmProvider
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.search import similarity_search

logger = logging.getLogger("ocs.documents.views")


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
        status = FileStatus.PENDING if collection.is_index else ""
        chunking_strategy = {}
        metadata = {}
        if collection.is_index:
            chunking_strategy = {
                "chunk_size": int(request.POST.get("chunk_size")),
                "chunk_overlap": int(request.POST.get("chunk_overlap")),
            }
            metadata["chunking_strategy"] = chunking_strategy

        collection_files = CollectionFile.objects.bulk_create(
            [CollectionFile(collection=collection, file=file, status=status, metadata=metadata) for file in files]
        )

    if collection.is_index:
        upload_files_to_vector_store_task.delay([f.id for f in collection_files], chuking_strategy=chunking_strategy)

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

    collection_file.file.delete_or_archive()
    if collection_file.collection.is_index:
        client = collection_file.collection.llm_provider.get_llm_service().get_raw_client()
        delete_file_from_openai(client, collection_file.file)

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

    def sync_openai_vector_store(
        self, collection: Collection, remove_old_vector_store: bool = False, old_llm_provider_id: int | None = None
    ):
        # TODO: Upload files to the new vector store
        try:
            if remove_old_vector_store and old_llm_provider_id and collection.openai_vector_store_id:
                old_provider_manager = VectorStoreManager.from_llm_provider(
                    LlmProvider.objects.get(id=old_llm_provider_id)
                )
                old_provider_manager.delete_vector_store(collection.openai_vector_store_id, fail_silently=True)

            manager = VectorStoreManager.from_llm_provider(collection.llm_provider)
            collection.openai_vector_store_id = manager.create_vector_store(
                name=f"collection-{collection.team.slug}-{collection.name}-{collection.id}"
            )
            collection.save(update_fields=["openai_vector_store_id"])
            messages.success(self.request, "Collection Created")
        except OpenAiSyncError as e:
            messages.error(self.request, f"Error syncing assistant to OpenAI: {e}")
        except Exception as e:
            logger.exception(f"Could not create vector store for collection {collection.id}. {e}")
            messages.error(self.request, "Could not create the vector store at OpenAI. Please try again later")


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

    @transaction.atomic()
    def form_valid(self, form):
        form.instance.team = self.request.team
        response = super().form_valid(form)
        if form.instance.is_index:
            self.sync_openai_vector_store(form.instance)

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

    @transaction.atomic()
    def form_valid(self, form):
        resposne = super().form_valid(form)
        if form.instance.is_index and "llm_provider" in form.changed_data:
            self.sync_openai_vector_store(
                form.instance, remove_old_vector_store=True, old_llm_provider_id=form.initial["llm_provider"]
            )
        return resposne


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
            if collection.is_index and collection.openai_vector_store_id:
                try:
                    manager = VectorStoreManager.from_llm_provider(collection.llm_provider)
                    manager.delete_vector_store(collection.openai_vector_store_id)
                    messages.success(request, "Collection deleted")
                except openai.NotFoundError:
                    messages.warning(
                        self.request, "Could not find the vector store at OpenAI. It may have been deleted already"
                    )
                except Exception as e:
                    logger.exception(f"Could not delete vector store for collection {collection.id}. {e}")
                    messages.error(self.request, "Could not delete the vector store at OpenAI. Please try again later")
                    return HttpResponse()

            collection.archive()
            return HttpResponse()
