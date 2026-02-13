from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
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
        )
        return {
            "type": "session",
            "messages": [{"role": role, "content": content} for role, content in chat_messages],
            "participant": item.session.participant.identifier,
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


class AnnotateQueue(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "human_annotations.add_annotation"

    def get(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
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
                "progress": progress,
                "item_content": item_content,
                "active_tab": "annotation_queues",
            },
        )


class AnnotateItem(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    """Show annotation form for a specific item (accessed from the items table)."""

    permission_required = "human_annotations.add_annotation"

    def get(self, request, team_slug: str, pk: int, item_pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        item = get_object_or_404(AnnotationItem, id=item_pk, queue=queue)

        already_annotated = Annotation.objects.filter(item=item, reviewer=request.user).exists()
        if already_annotated:
            messages.info(request, "You've already annotated this item.")
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
                "progress": progress,
                "item_content": item_content,
                "active_tab": "annotation_queues",
            },
        )


class SubmitAnnotation(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "human_annotations.add_annotation"

    def post(self, request, team_slug: str, pk: int, item_pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        item = get_object_or_404(AnnotationItem, id=item_pk, queue=queue)

        if Annotation.objects.filter(item=item, reviewer=request.user).exists():
            messages.warning(request, "You've already annotated this item.")
            return redirect("human_annotations:annotate_queue", team_slug=team_slug, pk=pk)

        FormClass = build_annotation_form(queue.schema)
        form = FormClass(request.POST)

        if form.is_valid():
            Annotation.objects.create(
                item=item,
                team=request.team,
                reviewer=request.user,
                data=form.cleaned_data,
                status=AnnotationStatus.SUBMITTED,
            )
            messages.success(request, "Annotation submitted.")
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
                    "progress": progress,
                    "item_content": item_content,
                    "active_tab": "annotation_queues",
                },
            )

        return redirect("human_annotations:annotate_queue", team_slug=team_slug, pk=pk)


class FlagItem(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "human_annotations.change_annotationitem"

    def post(self, request, team_slug: str, pk: int, item_pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        item = get_object_or_404(AnnotationItem, id=item_pk, queue=queue)
        item.status = AnnotationItemStatus.FLAGGED
        item.flag_reason = request.POST.get("flag_reason", "")
        item.save(update_fields=["status", "flag_reason"])
        messages.info(request, "Item flagged for review.")
        redirect_url = redirect("human_annotations:annotate_queue", team_slug=team_slug, pk=pk)
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_url.url
            return response
        return redirect_url
