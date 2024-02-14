from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.forms import modelform_factory
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views import View

from apps.files.models import File
from apps.teams.mixins import LoginAndTeamRequiredMixin


class DeleteFile(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "files.delete_file"

    @transaction.atomic()
    def delete(self, request, team_slug: str, pk: int):
        file = get_object_or_404(File, team=request.team, pk=pk)
        file.delete()
        # TODO: delete from external source e.g. openai
        messages.success(request, "File Deleted")
        return HttpResponse()


class BaseAddFileHtmxView(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "files.add_file"

    def post(self, request, team_slug: str, **kwargs):
        form = modelform_factory(File, fields=("file",))(
            request.POST,
            request.FILES,
        )
        if form.is_valid():
            file = self.form_valid(form)
            messages.success(request, "File Added")
            return render(
                request,
                "files/partials/file_item.html",
                {
                    "file": file,
                },
            )
        return HttpResponse(status=400)

    def form_valid(self, form):
        file = form.save(commit=False)
        file.team = self.request.team
        file.name = file.file.name
        file.save()
        return file
