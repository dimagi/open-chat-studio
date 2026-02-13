from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from apps.teams.mixins import LoginAndTeamRequiredMixin

from ..forms import build_annotation_form
from ..models import (
    Annotation,
    AnnotationItem,
    AnnotationItemStatus,
    AnnotationQueue,
    AnnotationStatus,
)


def _get_next_item(queue, user, skip_item_id=None):
    """Get the next item for this user to annotate: oldest pending/in-progress not already reviewed by user."""
    already_annotated = Annotation.objects.filter(
        item__queue=queue,
        reviewer=user,
    ).values_list("item_id", flat=True)

    qs = (
        AnnotationItem.objects.filter(
            queue=queue,
            status__in=[AnnotationItemStatus.PENDING, AnnotationItemStatus.IN_PROGRESS],
        )
        .exclude(id__in=already_annotated)
        .select_related("session__chat", "session__participant", "session__experiment", "message")
        .order_by("created_at")
    )
    if skip_item_id:
        qs = qs.exclude(id=skip_item_id)
    return qs.first()


def _get_progress_for_user(queue, user):
    """Get progress info for the current annotator."""
    total = queue.items.count()
    reviewed_by_user = Annotation.objects.filter(item__queue=queue, reviewer=user).count()
    return {"total": total, "reviewed": reviewed_by_user}


def _get_item_display_content(item):
    """Build display content dict for the annotation UI."""
    if item.session_id:
        chat_messages = item.session.chat.messages.order_by("created_at").values_list(
            "message_type",
            "content",
            "created_at",
        )
        session = item.session
        return {
            "type": "session",
            "messages": [
                {"role": role, "content": content, "created_at": created_at}
                for role, content, created_at in chat_messages
            ],
            "participant": session.participant.identifier,
            "participant_data": session.participant_data_from_experiment,
            "session_state": session.state,
        }
    elif item.message_id:
        msg = item.message
        return {
            "type": "message",
            "role": msg.message_type,
            "content": msg.content,
        }
    else:
        return {
            "type": "external",
            "data": item.external_data,
        }


def _check_assignee_access(queue, user):
    """Return True if user is allowed to annotate in this queue."""
    if not queue.assignees.exists():
        return True
    return queue.assignees.filter(id=user.id).exists()


class AnnotateQueue(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "human_annotations.add_annotation"

    def get(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        if not _check_assignee_access(queue, request.user):
            messages.error(request, "You are not assigned to this queue.")
            return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)
        skip_item_id = request.GET.get("skip")
        item = _get_next_item(queue, request.user, skip_item_id=skip_item_id)

        if item is None:
            messages.info(request, "No more items to annotate in this queue.")
            return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)

        FormClass = build_annotation_form(queue.schema)
        form = FormClass()
        progress = _get_progress_for_user(queue, request.user)
        item_content = _get_item_display_content(item)

        return render(
            request,
            "human_annotations/annotate.html",
            {
                "queue": queue,
                "item": item,
                "form": form,
                "can_annotate": True,
                "progress": progress,
                "item_content": item_content,
                "active_tab": "annotation_queues",
            },
        )


class AnnotateItem(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    """Show annotation form for a specific item (accessed from the items table)."""

    permission_required = "human_annotations.add_annotation"

    def get(self, request, team_slug: str, pk: int, item_pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        item = get_object_or_404(
            AnnotationItem.objects.select_related(
                "session__chat", "session__participant", "session__experiment", "message"
            ),
            id=item_pk,
            queue=queue,
        )

        can_annotate = _check_assignee_access(queue, request.user)
        already_annotated = Annotation.objects.filter(item=item, reviewer=request.user).exists()
        if already_annotated:
            can_annotate = False

        form = None
        annotations = []
        schema_fields = list(queue.schema.schema.keys())
        if can_annotate:
            FormClass = build_annotation_form(queue.schema)
            form = FormClass()
        else:
            annotations = [
                {
                    "reviewer": ann.reviewer,
                    "created_at": ann.created_at,
                    "fields": [(name, ann.data.get(name, "")) for name in schema_fields],
                }
                for ann in item.annotations.filter(status=AnnotationStatus.SUBMITTED)
                .select_related("reviewer")
                .order_by("created_at")
            ]

        progress = _get_progress_for_user(queue, request.user)
        item_content = _get_item_display_content(item)

        return render(
            request,
            "human_annotations/annotate.html",
            {
                "queue": queue,
                "item": item,
                "form": form,
                "can_annotate": can_annotate,
                "annotations": annotations,
                "progress": progress,
                "item_content": item_content,
                "active_tab": "annotation_queues",
            },
        )


class SubmitAnnotation(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "human_annotations.add_annotation"

    def post(self, request, team_slug: str, pk: int, item_pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        if not _check_assignee_access(queue, request.user):
            messages.error(request, "You are not assigned to this queue.")
            return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)
        item = get_object_or_404(AnnotationItem, id=item_pk, queue=queue)

        if Annotation.objects.filter(item=item, reviewer=request.user).exists():
            messages.warning(request, "You've already annotated this item.")
            return redirect("human_annotations:annotate_queue", team_slug=team_slug, pk=pk)

        FormClass = build_annotation_form(queue.schema)
        form = FormClass(request.POST)

        if form.is_valid():
            try:
                Annotation.objects.create(
                    item=item,
                    team=request.team,
                    reviewer=request.user,
                    data=form.cleaned_data,
                    status=AnnotationStatus.SUBMITTED,
                )
                messages.success(request, "Annotation submitted.")
            except IntegrityError:
                messages.warning(request, "You've already annotated this item.")
        else:
            messages.error(request, "Invalid annotation data. Please check the form.")
            item_content = _get_item_display_content(item)
            progress = _get_progress_for_user(queue, request.user)
            return render(
                request,
                "human_annotations/annotate.html",
                {
                    "queue": queue,
                    "item": item,
                    "form": form,
                    "can_annotate": True,
                    "progress": progress,
                    "item_content": item_content,
                    "active_tab": "annotation_queues",
                },
            )

        return redirect("human_annotations:annotate_queue", team_slug=team_slug, pk=pk)


class FlagItem(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "human_annotations.change_annotationitem"

    def post(self, request, team_slug: str, pk: int, item_pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        with transaction.atomic():
            item = AnnotationItem.objects.select_for_update().get(id=item_pk, queue=queue)
            item.status = AnnotationItemStatus.FLAGGED
            item.flags.append(
                {
                    "user": request.user.get_display_name(),
                    "user_id": request.user.id,
                    "reason": request.POST.get("flag_reason", ""),
                    "timestamp": timezone.now().isoformat(),
                }
            )
            item.save(update_fields=["status", "flags"])
        messages.info(request, "Item flagged for review.")
        redirect_url = redirect("human_annotations:annotate_queue", team_slug=team_slug, pk=pk)
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_url.url
            return response
        return redirect_url


class UnflagItem(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "human_annotations.change_annotationitem"

    def post(self, request, team_slug: str, pk: int, item_pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        with transaction.atomic():
            item = AnnotationItem.objects.select_for_update().get(id=item_pk, queue=queue)
            item.flags = []
            item.status = AnnotationItemStatus.PENDING  # Reset from FLAGGED so update_status recalculates
            item.save(update_fields=["flags", "status"])
            item.update_status()
        messages.info(request, "Item unflagged.")
        redirect_url = redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_url.url
            return response
        return redirect_url
