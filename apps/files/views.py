from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.forms import modelform_factory
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views import View

from apps.files.models import File
from apps.teams.mixins import LoginAndTeamRequiredMixin


class FileView(LoginAndTeamRequiredMixin, View):
    @method_decorator(permission_required("files.view_file"))
    def get(self, request, team_slug: str, pk: int):
        file = get_object_or_404(File, id=pk, team=request.team)
        try:
            return FileResponse(file.file.open(), as_attachment=True, filename=file.file.name)
        except FileNotFoundError:
            raise Http404()


class BaseAddFileHtmxView(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "files.add_file"

    def post(self, request, team_slug: str, **kwargs):
        form = modelform_factory(File, fields=("file",))(
            request.POST,
            request.FILES,
        )
        if form.is_valid():
            try:
                file = self.form_valid(form)
            except Exception as e:
                return self.get_error_response(e)
            return self.get_success_response(file)
        return HttpResponse(status=400)

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
