from contextlib import contextmanager
from functools import lru_cache

from django.db import transaction

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import Chat
from apps.experiments.models import ConsentForm, Experiment, ExperimentSession, Participant
from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.service_providers.models import LlmProviderModel
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


@lru_cache
def get_llm_provider_model(llm_provider_model_id: int):
    try:
        return LlmProviderModel.objects.get(id=llm_provider_model_id)
    except LlmProviderModel.DoesNotExist:
        raise PipelineNodeBuildError(f"LLM provider model with id {llm_provider_model_id} does not exist") from None
