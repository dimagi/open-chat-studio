from django.http import Http404
from django.shortcuts import get_object_or_404, render

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.sunsets import LEGACY_WEB_CHAT
from apps.teams.decorators import team_required
from apps.utils.decorators import sunset


@sunset(LEGACY_WEB_CHAT.sunset_at, successor_url=LEGACY_WEB_CHAT.successor_url)
@team_required
def rate_message(request, team_slug: str, message_id: int, rating: str):
    if rating not in ["👍", "👎"]:
        raise Http404()

    message = get_object_or_404(ChatMessage, id=message_id, message_type=ChatMessageType.AI, chat__team=request.team)
    message.add_rating(rating)

    return render(
        request,
        template_name="experiments/chat/components/message_rating.html",
        context={
            "team_slug": team_slug,
            "message": message,
        },
    )
