import logging

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template import loader
from django.utils.decorators import method_decorator
from django.views import View

from apps.files.forms import MultipleFileFieldForm
from apps.files.models import File
from apps.teams.mixins import LoginAndTeamRequiredMixin

logger = logging.getLogger(__name__)


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
