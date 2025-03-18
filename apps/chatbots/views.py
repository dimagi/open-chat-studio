from django.contrib.auth.decorators import permission_required
from django.views.generic import CreateView

from apps.chatbots.tables import ChatbotTable
from apps.experiments.models import Experiment
from apps.experiments.views.experiment import BaseExperimentView
from apps.generics.views import generic_home
from apps.teams.decorators import login_and_team_required
from apps.utils.BaseExperimentTableView import BaseExperimentTableView


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def chatbots_home(request, team_slug: str):
    return generic_home(request, team_slug, "Chatbots", "chatbots:table", "chatbots:new")


class ChatbotExperimentTableView(BaseExperimentTableView):
    model = Experiment
    table_class = ChatbotTable
    permission_required = "experiments.view_experiment"

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(pipeline__isnull=False)


# TODO: New chatbot to be implemented as part of #1307
class CreateChatbot(BaseExperimentView, CreateView):
    def create_experiment(self):
        return None
