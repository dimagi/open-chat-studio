from contextlib import contextmanager
from string import Formatter

from django.db import transaction
from langchain_core.messages import SystemMessage

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import Chat
from apps.experiments.models import ConsentForm, Experiment, ExperimentSession, Participant
from apps.pipelines.exceptions import PipelineNodeRunError
from apps.service_providers.llm_service.prompt_context import PromptTemplateContext
from apps.teams.models import Team
from apps.teams.utils import current_team
from apps.users.models import CustomUser


@contextmanager
def temporary_session(team: Team, user_id: int):
    """A temporary sesssion setup that is rolled back after the context exits."""
    with current_team(team), transaction.atomic():
        user = CustomUser.objects.get(id=user_id)
        consent_form = ConsentForm.get_default(team)
        experiment = Experiment.objects.create(
            team=team, name="Temporary Experiment", owner=user, consent_form=consent_form
        )
        channel = ExperimentChannel.objects.get_team_web_channel(team)
        chat = Chat.objects.create(team=team, name="Temporary Chat")
        participant = Participant.create_anonymous(team=team, platform=ChannelPlatform.WEB)
        experiment_session = ExperimentSession.objects.create(
            team=team,
            experiment=experiment,
            chat=chat,
            experiment_channel=channel,
            participant=participant,
        )
        yield experiment_session
        transaction.set_rollback(True)


def get_system_message(prompt_template: str, prompt_context: PromptTemplateContext) -> SystemMessage:
    """
    Returns a populated SystemMessage based on the provided prompt template and context.
    """
    input_variables = {v for _, v, _, _ in Formatter().parse(prompt_template) if v is not None}
    context = prompt_context.get_context(input_variables)
    try:
        system_message = prompt_template.format(**context)
        return SystemMessage(content=system_message)
    except KeyError as e:
        raise PipelineNodeRunError(str(e)) from e
