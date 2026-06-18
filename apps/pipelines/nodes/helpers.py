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
            platform=channel.platform,
        )
        yield experiment_session
        transaction.set_rollback(True)


def prompt_uses_current_datetime(prompt_template: str) -> bool:
    """Whether ``{current_datetime}`` appears in the prompt template."""
    return any(
        field_name == "current_datetime" for _, field_name, _, _ in Formatter().parse(prompt_template) if field_name
    )


def get_system_message(prompt_template: str, prompt_context: PromptTemplateContext) -> SystemMessage:
    """
    Returns a populated SystemMessage based on the provided prompt template and context.
    """
    input_variables = {v for _, v, _, _ in Formatter().parse(prompt_template) if v is not None}
    context = prompt_context.get_context(input_variables)
    if "current_datetime" in context:
        # Render the volatile second-precision datetime at day precision so the cached system
        # prompt prefix stays stable within a day. The precise time is injected into the latest
        # message turn instead (see apps.pipelines.nodes.llm_node). See issue #3625.
        context["current_datetime"] = prompt_context.get_current_date()
    try:
        system_message = prompt_template.format(**context)
        return SystemMessage(content=system_message)
    except KeyError as e:
        raise PipelineNodeRunError(str(e)) from e


def get_agent_middleware(node, system_message: SystemMessage) -> list:
    """Returns the common agent middleware for nodes that build LLM agents:
    history compression and provider prompt caching.
    """
    middleware = []
    if history_middleware := node.build_history_middleware(system_message=system_message):
        middleware.append(history_middleware)
    if caching_middleware := node.get_llm_service().get_prompt_caching_middleware():
        middleware.append(caching_middleware)
    return middleware
