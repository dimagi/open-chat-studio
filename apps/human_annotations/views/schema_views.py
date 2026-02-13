from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.teams.mixins import LoginAndTeamRequiredMixin

from ..forms import AnnotationSchemaForm
from ..models import AnnotationSchema
from ..tables import AnnotationSchemaTable


class AnnotationSchemaHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    template_name = "generic/object_home.html"
    permission_required = "human_annotations.view_annotationschema"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "annotation_schemas",
            "title": "Annotation Schemas",
            "new_object_url": reverse("human_annotations:schema_new", args=[team_slug]),
            "table_url": reverse("human_annotations:schema_table", args=[team_slug]),
            "enable_search": True,
        }


class AnnotationSchemaTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    model = AnnotationSchema
    table_class = AnnotationSchemaTable
    template_name = "table/single_table.html"
    permission_required = "human_annotations.view_annotationschema"

    def get_queryset(self):
        return AnnotationSchema.objects.filter(team=self.request.team)


class CreateAnnotationSchema(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    permission_required = "human_annotations.add_annotationschema"
    model = AnnotationSchema
    form_class = AnnotationSchemaForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Annotation Schema",
        "button_text": "Create",
        "active_tab": "annotation_schemas",
    }

    def get_success_url(self):
        return reverse("human_annotations:schema_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        return super().form_valid(form)


class EditAnnotationSchema(LoginAndTeamRequiredMixin, UpdateView, PermissionRequiredMixin):
    permission_required = "human_annotations.change_annotationschema"
    model = AnnotationSchema
    form_class = AnnotationSchemaForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Edit Annotation Schema",
        "button_text": "Update",
        "active_tab": "annotation_schemas",
    }

    def get_queryset(self):
        return AnnotationSchema.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("human_annotations:schema_home", args=[self.request.team.slug])


class DeleteAnnotationSchema(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "human_annotations.delete_annotationschema"

    def delete(self, request, team_slug: str, pk: int):
        schema = get_object_or_404(AnnotationSchema, id=pk, team=request.team)
        if schema.queues.exists():
            messages.error(request, "Cannot delete schema that is in use by queues.")
            return HttpResponse(status=400)
        schema.delete()
        messages.success(request, "Schema deleted")
        return HttpResponse()
