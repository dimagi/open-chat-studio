from waffle import flag_is_active

from apps.experiments.models import SyntheticVoice
from apps.teams.flags import Flags


def excluded_voice_services(request) -> list[str]:
    """The voice services this request may not use.

    OpenAI Voice Engine is gated on ``flag_open_ai_voice_engine``: without the flag the chatbot
    settings page offers neither those voices nor the providers that hold them, so the write API
    must refuse them too. Accepting one would wire the chatbot to a voice the settings form's own
    querysets exclude, and from then on saving that page fails validation on a field the user never
    touched.

    Shared by ``ChatbotSettingsForm`` and the API serializers so the two cannot drift, and takes a
    request rather than a team because ``Flag.is_active`` is waffle's full predicate -- ``everyone``,
    ``percent``, ``superusers``, the user and group lists -- *or* our team override. Checking only
    the team half would leave the settings page offering voices the API rejects the moment the flag
    is switched on any other way, which is what a normal ``everyone=True`` rollout does.
    """
    if flag_is_active(request, Flags.OPEN_AI_VOICE_ENGINE.slug):
        return []
    return [SyntheticVoice.OpenAIVoiceEngine]


def get_real_user_or_none(user):
    if user.is_anonymous:
        return None
    else:
        return user
