from django.urls import reverse
from django.utils.translation import gettext as _

from apps.experiments.models import Experiment
from apps.generics.breadcrumbs import Crumb


def chatbot_crumbs(team_slug: str, experiment: Experiment) -> list[Crumb]:
    return [
        (_("Chatbots"), reverse("chatbots:chatbots_home", args=[team_slug])),
        (experiment.name, reverse("chatbots:single_chatbot_home", args=[team_slug, experiment.id])),
    ]
