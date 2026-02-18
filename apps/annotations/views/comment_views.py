import json

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views import View

from apps.annotations.models import UserComment
from apps.teams.mixins import LoginAndTeamRequiredMixin


class LinkComment(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "annotations.add_usercomment"

    def post(self, request, team_slug: str):
        object_info = json.loads(request.POST["object_info"])
        object_id = object_info["id"]
        content_type = get_object_or_404(ContentType, app_label=object_info["app"], model=object_info["model_name"])
        target_object = content_type.get_object_for_this_type(id=object_id)
        comment = UserComment.add_for_model(
            target_object, comment=request.POST["comment"], added_by=request.user, team=request.team
        )
        if comment is None:
            messages.error(request, f"Unable to add comment to {content_type.model}")
            return HttpResponse("Unprocessable Entity", status=422)
        return render(
            request,
            "experiments/components/user_comments.html",
            context={"object": target_object, "update_count": True},
        )


class UnlinkComment(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "annotations.delete_usercomment"

    def post(self, request, team_slug: str):
        object_info = json.loads(request.POST["object_info"])
        object_id = object_info["id"]
        content_type = get_object_or_404(ContentType, app_label=object_info["app"], model=object_info["model_name"])
        target_object = content_type.get_object_for_this_type(id=object_id)
        UserComment.objects.get(id=request.POST["comment_id"], team__slug=team_slug).delete()
        return render(
            request,
            "experiments/components/user_comments.html",
            context={"object": target_object, "update_count": True},
        )
