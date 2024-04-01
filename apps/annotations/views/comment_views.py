import json

from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404, render
from django.views import View

from apps.annotations.models import UserComment
from apps.teams.mixins import LoginAndTeamRequiredMixin


class LinkComment(LoginAndTeamRequiredMixin, View):
    def post(self, request, team_slug: str):
        object_info = json.loads(request.POST["object_info"])
        object_id = object_info["id"]
        content_type = get_object_or_404(ContentType, app_label=object_info["app"], model=object_info["model_name"])
        chat_message = content_type.get_object_for_this_type(id=object_id)
        UserComment.add_for_model(
            chat_message, comment=request.POST["comment"], added_by=request.user, team=request.team
        )
        return render(request, "experiments/components/user_comments.html", context={"message": chat_message})


class UnlinkComment(LoginAndTeamRequiredMixin, View):
    def post(self, request, team_slug: str):
        object_info = json.loads(request.POST["object_info"])
        object_id = object_info["id"]
        content_type = get_object_or_404(ContentType, app_label=object_info["app"], model=object_info["model_name"])
        chat_message = content_type.get_object_for_this_type(id=object_id)
        UserComment.objects.get(id=request.POST["comment_id"]).delete()
        return render(request, "experiments/components/user_comments.html", context={"message": chat_message})
