import contextlib
import csv
import json
import re
import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import IntegrityError
from django.db.models import Count, Exists, OuterRef, Prefetch, Subquery, Sum
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView
from django_tables2 import LazyPaginator, SingleTableView
from waffle import flag_is_active

from apps.chat.models import ChatMessage
from apps.experiments.filters import ExperimentSessionFilter, get_filter_context_data
from apps.experiments.models import ExperimentSession
from apps.filters.models import FilterSet
from apps.teams.decorators import login_and_team_required
from apps.teams.flags import Flags
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.web.dynamic_filters.datastructures import FilterParams

from ..forms import AnnotationQueueForm
from ..models import Annotation, AnnotationItem, AnnotationItemType, AnnotationQueue, AnnotationStatus, QueueStatus
from ..tables import AnnotationItemTable, AnnotationQueueTable, AnnotationSessionsSelectionTable

User = get_user_model()


def _safe_filename(name: str) -> str:
    """Sanitize a string for use in Content-Disposition filename."""
    return re.sub(r"[^\w\s\-.]", "_", name).strip()


class AnnotationQueueHome(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"
    permission_required = "human_annotations.view_annotationqueue"

    def get_context_data(self, **kwargs):
        team_slug = self.kwargs["team_slug"]
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
    template_name = "human_annotations/queue_form.html"
    extra_context = {
        "title": "Create Annotation Queue",
        "button_text": "Create",
        "active_tab": "annotation_queues",
    }

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
    template_name = "human_annotations/queue_form.html"
    extra_context = {
        "title": "Edit Annotation Queue",
        "button_text": "Update",
        "active_tab": "annotation_queues",
    }

    def get_queryset(self):
        return AnnotationQueue.objects.filter(team=self.request.team)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["existing_schema"] = self.object.schema
        context["schema_locked"] = self.object.items.filter(review_count__gt=0).exists()
        return context

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
        return AnnotationQueue.objects.filter(team=self.request.team).select_related("aggregate")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queue = self.object
        context["active_tab"] = "annotation_queues"
        context["progress"] = queue.get_progress()
        context["items_table_url"] = reverse(
            "human_annotations:queue_items_table",
            args=[self.request.team.slug, queue.pk],
        )

        aggregate = getattr(queue, "aggregate", None)
        context["aggregates"] = aggregate.aggregates if aggregate else {}

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
                    queryset=Annotation.objects.filter(status=AnnotationStatus.SUBMITTED).select_related("reviewer"),
                    to_attr="submitted_annotations",
                ),
            )
        )


def _get_base_session_queryset(request):
    """Returns a team-scoped, filtered session queryset with no annotations or related selection."""
    timezone = request.session.get("detected_tz", None)
    filter_params = FilterParams.from_request(request)
    queryset = ExperimentSession.objects.filter(team=request.team)
    session_filter = ExperimentSessionFilter()
    return session_filter.apply(queryset, filter_params=filter_params, timezone=timezone)


class AnnotationQueueSessionsTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):
    """Filterable, paginated session table for selecting sessions to add to a queue."""

    model = ExperimentSession
    table_class = AnnotationSessionsSelectionTable
    template_name = "table/single_table_lazy_pagination.html"
    permission_required = "human_annotations.add_annotationitem"
    paginator_class = LazyPaginator

    def get_queryset(self):
        # Validate queue ownership — pk is in the URL for namespacing but not used for filtering.
        get_object_or_404(AnnotationQueue, id=self.kwargs["pk"], team=self.request.team)
        queryset = _get_base_session_queryset(self.request)
        message_count_sq = (
            ChatMessage.objects.filter(chat=OuterRef("chat")).values("chat").annotate(c=Count("id")).values("c")
        )
        return (
            queryset.annotate(message_count=Coalesce(Subquery(message_count_sq), 0))
            .filter(message_count__gt=0)
            .select_related("team", "participant__user", "chat", "experiment")
            .order_by("-last_activity_at")
        )


@login_and_team_required
@permission_required("human_annotations.add_annotationitem", raise_exception=True)
def annotation_queue_sessions_json(request, team_slug: str, pk: int):
    """Returns filtered session external_ids as JSON for the Alpine session selector.

    pk is validated for queue ownership but not used for filtering — returns all matching
    team sessions excluding those already in the queue.
    """
    # Validate queue ownership
    get_object_or_404(AnnotationQueue, id=pk, team=request.team)
    queryset = _get_base_session_queryset(request)
    # Exclude sessions already added to this queue so the count reflects available sessions.
    queryset = queryset.exclude(id__in=AnnotationItem.objects.filter(queue_id=pk).values("session_id"))
    queryset = queryset.filter(Exists(ChatMessage.objects.filter(chat=OuterRef("chat"))))
    session_keys = list(queryset.values_list("external_id", flat=True))
    return JsonResponse(session_keys, safe=False)


class AddSessionsToQueue(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "human_annotations.add_annotationitem"

    def get(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        table_url = reverse("human_annotations:queue_sessions_table", args=[team_slug, pk])
        sessions_json_url = reverse("human_annotations:queue_sessions_json", args=[team_slug, pk])
        filter_context = get_filter_context_data(
            request.team,
            columns=ExperimentSessionFilter.columns(request.team),
            filter_class=ExperimentSessionFilter,
            table_url=table_url,
            table_container_id="sessions-table",
            table_type=FilterSet.TableType.SESSIONS,
        )
        return render(
            request,
            "human_annotations/add_items_from_sessions.html",
            {
                "queue": queue,
                "sessions_json_url": sessions_json_url,
                "active_tab": "annotation_queues",
                **filter_context,
            },
        )

    def post(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        session_ids_raw = request.POST.get("session_ids", "")
        # Silently skip non-UUID values to avoid ORM errors from tampered form data.
        external_ids = []
        for s in session_ids_raw.split(","):
            s = s.strip()
            if s:
                with contextlib.suppress(ValueError):
                    external_ids.append(str(uuid.UUID(s)))

        if not external_ids:
            messages.error(request, "No sessions selected.")
            return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)

        sessions = list(ExperimentSession.objects.filter(external_id__in=external_ids, team=request.team))
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


class AddSessionToQueueFromSession(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "human_annotations.add_annotationitem"

    def dispatch(self, request, *args, **kwargs):
        if not flag_is_active(request, Flags.HUMAN_ANNOTATIONS.slug):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, team_slug: str, session_id: str):
        session = get_object_or_404(ExperimentSession, external_id=session_id, team=request.team)
        queues = list(
            AnnotationQueue.objects.filter(team=request.team, status=QueueStatus.ACTIVE).order_by("name")[:50]
        )
        already_queued_ids = set(
            AnnotationItem.objects.filter(
                session=session,
                queue__in=queues,
            ).values_list("queue_id", flat=True)
        )
        return render(
            request,
            "human_annotations/add_session_to_queue_modal.html",
            {
                "session": session,
                "queues": queues,
                "already_queued_ids": already_queued_ids,
            },
        )

    def post(self, request, team_slug: str, session_id: str):
        session = get_object_or_404(ExperimentSession, external_id=session_id, team=request.team)
        try:
            queue_id = int(request.POST.get("queue_id", ""))
        except (ValueError, TypeError):
            queue_id = None
        if not queue_id:
            return render(
                request,
                "human_annotations/add_session_to_queue_result.html",
                {"error": _("Please select a queue.")},
            )
        queue = get_object_or_404(AnnotationQueue, id=queue_id, team=request.team, status=QueueStatus.ACTIVE)
        try:
            item, created = AnnotationItem.objects.get_or_create(
                queue=queue,
                session=session,
                defaults={
                    "team": request.team,
                    "item_type": AnnotationItemType.SESSION,
                },
            )
        except IntegrityError:
            # Concurrent POST race: another request inserted the row between our SELECT and INSERT.
            created = False
        return render(
            request,
            "human_annotations/add_session_to_queue_result.html",
            {
                "queue": queue,
                "created": created,
            },
        )


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
            status=AnnotationStatus.SUBMITTED,
        ).select_related("item", "item__session", "item__message", "reviewer")

        if export_format == "jsonl":
            return self._export_jsonl(queue, annotations)
        return self._export_csv(queue, annotations)

    def _export_csv(self, queue, annotations):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{_safe_filename(queue.name)}_annotations.csv"'

        schema_fields = list(queue.schema.keys())
        fieldnames = ["item_id", "item_type", "reviewer", "annotated_at"] + schema_fields

        writer = csv.DictWriter(response, fieldnames=fieldnames)
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
            lines.append(json.dumps(record))

        content = "\n".join(lines)
        response = HttpResponse(content, content_type="application/jsonl")
        response["Content-Disposition"] = f'attachment; filename="{_safe_filename(queue.name)}_annotations.jsonl"'
        return response
