import logging

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template import loader
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_htmx.http import reswap
from django_tables2 import SingleTableView

from apps.documents.models import CollectionFile
from apps.files.forms import FileForm, MultipleFileFieldForm
from apps.files.models import File
from apps.files.tables import FilesTable
from apps.generics.chips import Chip
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.search import similarity_search

logger = logging.getLogger("ocs.files")


class FileView(LoginAndTeamRequiredMixin, View):
    @method_decorator(permission_required("files.view_file"))
    def get(self, request, team_slug: str, pk: int):
        def _not_found():
            referrer = request.GET.get("from")
            if referrer and referrer.startswith("/"):
                messages.error(request, "Unable to read file contents.")
                return redirect(referrer)
            raise Http404()

        file = get_object_or_404(File, id=pk, team=request.team)
        if not file.file:
            return _not_found()

        try:
            return FileResponse(file.file.open(), as_attachment=True, filename=file.file.name)
        except FileNotFoundError:
            return _not_found()


class BaseAddFileHtmxView(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "files.add_file"

    def post(self, request, team_slug: str, **kwargs):
        form = forms.modelform_factory(File, fields=("file",))(
            request.POST,
            request.FILES,
        )
        if form.is_valid():
            try:
                file = self.form_valid(form)
            except Exception as e:
                logger.exception("Error saving file")
                return self.get_error_response(e)
            return self.get_success_response(file)
        return self.get_error_response(form.errors.as_text())

    def get_success_response(self, file):
        return render(
            self.request,
            "files/partials/file_item.html",
            {
                "file": file,
                "delete_url": self.get_delete_url(file),
            },
        )

    def get_error_response(self, error):
        messages.error(self.request, "Error uploading file")
        return render(
            self.request,
            "files/partials/file_item_error.html",
            {
                "error": error,
            },
        )

    def get_delete_url(self, file):
        raise NotImplementedError()

    def form_valid(self, form):
        file = form.save(commit=False)
        file.team = self.request.team
        file.save()
        return file


class BaseAddMultipleFilesHtmxView(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "files.add_file"

    def post(self, request, team_slug: str, **kwargs):
        form = MultipleFileFieldForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                files = self.form_valid(form)
            except Exception as e:
                logger.exception("Error saving file")
                return self.get_error_response(e)
            return self.get_success_response(files)
        return self.get_error_response(form.errors.as_text())

    def get_success_response(self, files):
        content = ""
        for file in files:
            context = {
                "file": file,
                "delete_url": self.get_delete_url(file),
            }
            content += loader.render_to_string("files/partials/file_item.html", context, self.request)
        return HttpResponse(content)

    def get_error_response(self, error):
        messages.error(self.request, "Error uploading files")
        return render(
            self.request,
            "files/partials/file_item_error.html",
            {
                "error": error,
            },
        )

    def get_delete_url(self, file):
        raise NotImplementedError()

    def form_valid(self, form):
        files = form.cleaned_data["file"]
        return File.objects.bulk_create(
            [
                File(
                    team=self.request.team,
                    name=f.name,
                    file=f,
                    content_size=f.size,
                    content_type=File.get_content_type(f),
                )
                for f in files
            ]
        )


class BaseDeleteFileView(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "files.delete_file"

    @transaction.atomic()
    def delete(self, request, team_slug: str, **kwargs):
        file_id = kwargs["file_id"]
        file = get_object_or_404(File, team=request.team, pk=file_id)
        file.delete()
        return self.get_success_response(file)

    def get_success_response(self, file):
        messages.success(self.request, "File Deleted")
        return HttpResponse()


class FileHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, **kwargs):
        team_slug = self.kwargs["team_slug"]
        return {
            "active_tab": "files",
            "title": "Files",
            "table_url": reverse("files:file_table", args=[team_slug]),
            "enable_search": True,
            "allow_new": False,
        }


class FileTableView(LoginAndTeamRequiredMixin, SingleTableView):
    model = File
    table_class = FilesTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        is_collection_file = Exists(CollectionFile.objects.filter(file_id=OuterRef("id")))
        queryset = (
            File.objects.filter(is_collection_file)
            .filter(team=self.request.team, is_version=False)
            .order_by("-created_at")
        )

        if search := self.request.GET.get("search"):
            queryset = similarity_search(queryset, search_phase=search, columns=["name", "summary"], score=0.1)
        return queryset


# This view is not currently being used
class CreateFile(LoginAndTeamRequiredMixin, CreateView):
    template_name = "documents/file_form.html"
    model = File
    form_class = FileForm
    permission_required = "files.add_file"
    extra_context = {
        "title": "Upload File",
        "button_text": "Upload",
        "active_tab": "files",
        "form_attrs": {"enctype": "multipart/form-data"},
    }

    def get_success_url(self):
        return reverse("files:file_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        response = super().form_valid(form)
        messages.success(self.request, "File uploaded successfully")
        return response


class EditFile(LoginAndTeamRequiredMixin, UpdateView):
    template_name = "documents/file_form.html"
    model = File
    form_class = FileForm
    permission_required = "files.change_file"
    extra_context = {
        "title": "Edit File",
        "button_text": "Update",
        "active_tab": "files",
        "form_attrs": {"enctype": "multipart/form-data"},
    }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add collection chips for the file
        collections = self.object.get_collection_references()
        context["collection_chips"] = [Chip(label=col.name, url=col.get_absolute_url()) for col in collections]
        return context

    def get_queryset(self):
        return File.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("files:file_home", args=[self.request.team.slug])

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.success(request, "File updated successfully")
        return response


class DeleteFile(LoginAndTeamRequiredMixin, View):
    permission_required = "files.delete_file"

    def delete(self, request, team_slug: str, pk: int):
        file = get_object_or_404(File, team__slug=team_slug, id=pk)

        if collections := file.get_collection_references():
            response = render_to_string(
                "generic/referenced_objects.html",
                context={
                    "object_name": "file",
                    "pipeline_nodes": [Chip(label=col.name, url=col.get_absolute_url()) for col in collections],
                },
            )
            return reswap(HttpResponse(response, status=400), "none")
        else:
            file.delete_or_archive()
            messages.success(request, "File deleted")
            return HttpResponse()
