from django.contrib.auth.decorators import permission_required
from django.views.generic import CreateView

from apps.chatbots.tables import ChatbotTable
from apps.experiments.models import Experiment
from apps.experiments.views.experiment import BaseExperimentView
from apps.teams.decorators import login_and_team_required
from apps.utils.BaseTableView import BaseTableView
from apps.utils.helpers import generic_home


@login_and_team_required
@permission_required("chatbots.view_chatbot", raise_exception=True)
def chatbots_home(request, team_slug: str):
    return generic_home(request, team_slug, "Chatbots", "chatbots:table", "chatbots:new")


class ChatbotTableView(BaseTableView):
    model = Experiment
    table_class = ChatbotTable
    permission_required = "chatbots.view_chatbot"

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(pipeline__isnull=False)

# TODO: New chatbot to be implemented as part of #1307
class CreateChatbot(BaseExperimentView, CreateView):
    def create_experiment(self):
        return None
