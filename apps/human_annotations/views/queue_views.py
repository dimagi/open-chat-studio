import csv as csv_module
import io
import json
import re

from bs4 import UnicodeDammit
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Count, Prefetch, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.teams.mixins import LoginAndTeamRequiredMixin

from ..forms import AnnotationQueueForm
from ..models import Annotation, AnnotationItem, AnnotationItemType, AnnotationQueue
from ..tables import AnnotationItemTable, AnnotationQueueTable

User = get_user_model()


def _safe_filename(name: str) -> str:
    """Sanitize a string for use in Content-Disposition filename."""
    return re.sub(r"[^\w\s\-.]", "_", name).strip()


class AnnotationQueueHome(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"
    permission_required = "human_annotations.view_annotationqueue"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "annotation_queues",
            "title": "Annotation Queues",
            "new_object_url": reverse("human_annotations:queue_new", args=[team_slug]),
            "table_url": reverse("human_annotations:queue_table", args=[team_slug]),
            "enable_search": True,
        }


class AnnotationQueueTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):
    model = AnnotationQueue
    table_class = AnnotationQueueTable
    template_name = "table/single_table.html"
    permission_required = "human_annotations.view_annotationqueue"

    def get_queryset(self):
        return AnnotationQueue.objects.filter(team=self.request.team).annotate(
            _total_items=Count("items"),
            _reviews_done=Sum("items__review_count"),
        )


class CreateAnnotationQueue(LoginAndTeamRequiredMixin, PermissionRequiredMixin, CreateView):
    permission_required = "human_annotations.add_annotationqueue"
    model = AnnotationQueue
    form_class = AnnotationQueueForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Annotation Queue",
        "button_text": "Create",
        "active_tab": "annotation_queues",
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["team"] = self.request.team
        return kwargs

    def get_success_url(self):
        return reverse("human_annotations:queue_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class EditAnnotationQueue(LoginAndTeamRequiredMixin, PermissionRequiredMixin, UpdateView):
    permission_required = "human_annotations.change_annotationqueue"
    model = AnnotationQueue
    form_class = AnnotationQueueForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Edit Annotation Queue",
        "button_text": "Update",
        "active_tab": "annotation_queues",
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["team"] = self.request.team
        return kwargs

    def get_queryset(self):
        return AnnotationQueue.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("human_annotations:queue_home", args=[self.request.team.slug])


class DeleteAnnotationQueue(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "human_annotations.delete_annotationqueue"

    def delete(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        queue.delete()
        messages.success(request, "Queue deleted")
        return HttpResponse()


class AnnotationQueueDetail(LoginAndTeamRequiredMixin, PermissionRequiredMixin, DetailView):
    model = AnnotationQueue
    template_name = "human_annotations/queue_detail.html"
    permission_required = "human_annotations.view_annotationqueue"

    def get_queryset(self):
        return AnnotationQueue.objects.filter(team=self.request.team)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queue = self.object
        context["active_tab"] = "annotation_queues"
        context["progress"] = queue.get_progress()
        context["items_table_url"] = reverse(
            "human_annotations:queue_items_table",
            args=[self.request.team.slug, queue.pk],
        )
        return context


class AnnotationQueueItemsTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):
    model = AnnotationItem
    table_class = AnnotationItemTable
    template_name = "table/single_table.html"
    permission_required = "human_annotations.view_annotationqueue"

    def get_queryset(self):
        return (
            AnnotationItem.objects.filter(
                queue_id=self.kwargs["pk"],
                queue__team=self.request.team,
            )
            .select_related("session", "message", "queue")
            .prefetch_related(
                Prefetch(
                    "annotations",
                    queryset=Annotation.objects.filter(status="submitted").select_related("reviewer"),
                    to_attr="submitted_annotations",
                ),
            )
        )


class AddSessionsToQueue(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "human_annotations.add_annotationitem"

    def get(self, request, team_slug: str, pk: int):
        from apps.experiments.models import ExperimentSession

        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        sessions = (
            ExperimentSession.objects.filter(team=request.team)
            .select_related("experiment", "participant", "chat")
            .order_by("-last_activity_at")[:200]
        )
        return render(
            request,
            "human_annotations/add_items_from_sessions.html",
            {
                "queue": queue,
                "sessions": sessions,
                "active_tab": "annotation_queues",
            },
        )

    def post(self, request, team_slug: str, pk: int):
        from apps.experiments.models import ExperimentSession

        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        session_ids = request.POST.getlist("sessions")

        if not session_ids:
            messages.error(request, "No sessions selected.")
            return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)

        sessions = list(ExperimentSession.objects.filter(id__in=session_ids, team=request.team))
        existing_session_ids = set(
            AnnotationItem.objects.filter(
                queue=queue,
                session__in=sessions,
            ).values_list("session_id", flat=True)
        )

        items_to_create = [
            AnnotationItem(
                queue=queue,
                team=request.team,
                item_type=AnnotationItemType.SESSION,
                session=session,
            )
            for session in sessions
            if session.id not in existing_session_ids
        ]
        created = AnnotationItem.objects.bulk_create(items_to_create, ignore_conflicts=True)
        skipped = len(sessions) - len(created)

        msg = f"Added {len(created)} items to queue."
        if skipped:
            msg += f" Skipped {skipped} duplicates."
        messages.success(request, msg)
        return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)


class ImportCSVToQueue(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "human_annotations.add_annotationitem"

    def get(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        return render(
            request,
            "human_annotations/import_csv.html",
            {
                "queue": queue,
                "active_tab": "annotation_queues",
            },
        )

    def post(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        csv_file = request.FILES.get("csv_file")

        if not csv_file:
            messages.error(request, "No file uploaded.")
            return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)

        max_rows = 10_000
        content = csv_file.read()
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            detected = UnicodeDammit(content).unicode_markup
            if detected is None:
                messages.error(request, "Unable to detect file encoding. Please upload a UTF-8 encoded CSV file.")
                return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)
            decoded = detected
        reader = csv_module.DictReader(io.StringIO(decoded))

        items = []
        for i, row in enumerate(reader):
            if i >= max_rows:
                messages.warning(request, f"Only the first {max_rows} rows were imported.")
                break
            items.append(
                AnnotationItem(
                    queue=queue,
                    team=request.team,
                    item_type=AnnotationItemType.EXTERNAL,
                    external_data=dict(row),
                )
            )
        AnnotationItem.objects.bulk_create(items)
        messages.success(request, f"Imported {len(items)} items from CSV.")
        return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)


class ManageAssignees(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "human_annotations.change_annotationqueue"

    def get(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        team_members = User.objects.filter(membership__team=request.team)
        current_assignees = set(queue.assignees.values_list("id", flat=True))
        return render(
            request,
            "human_annotations/manage_assignees.html",
            {
                "queue": queue,
                "team_members": team_members,
                "current_assignees": current_assignees,
                "active_tab": "annotation_queues",
            },
        )

    def post(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        assignee_ids = request.POST.getlist("assignees")
        users = User.objects.filter(id__in=assignee_ids, membership__team=request.team)
        queue.assignees.set(users)
        messages.success(request, "Assignees updated.")
        return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)


class ExportAnnotations(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "human_annotations.view_annotation"

    def get(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        export_format = request.GET.get("format", "csv")
        annotations = Annotation.objects.filter(
            item__queue=queue,
            status="submitted",
        ).select_related("item", "item__session", "item__message", "reviewer")

        if export_format == "jsonl":
            return self._export_jsonl(queue, annotations)
        return self._export_csv(queue, annotations)

    def _export_csv(self, queue, annotations):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{_safe_filename(queue.name)}_annotations.csv"'

        schema_fields = list(queue.schema.schema.keys())
        fieldnames = ["item_id", "item_type", "reviewer", "annotated_at"] + schema_fields

        writer = csv_module.DictWriter(response, fieldnames=fieldnames)
        writer.writeheader()

        for ann in annotations:
            row = {
                "item_id": ann.item_id,
                "item_type": ann.item.item_type,
                "reviewer": ann.reviewer.get_full_name() or ann.reviewer.username,
                "annotated_at": ann.created_at.isoformat(),
            }
            for field in schema_fields:
                row[field] = ann.data.get(field, "")
            writer.writerow(row)

        return response

    def _export_jsonl(self, queue, annotations):
        lines = []
        for ann in annotations:
            record = {
                "item_id": ann.item_id,
                "item_type": ann.item.item_type,
                "reviewer": ann.reviewer.get_full_name() or ann.reviewer.username,
                "annotated_at": ann.created_at.isoformat(),
                "annotation": ann.data,
            }
            if ann.item.external_data:
                record["external_data"] = ann.item.external_data
            lines.append(json.dumps(record))

        content = "\n".join(lines)
        response = HttpResponse(content, content_type="application/jsonl")
        response["Content-Disposition"] = f'attachment; filename="{_safe_filename(queue.name)}_annotations.jsonl"'
        return response
