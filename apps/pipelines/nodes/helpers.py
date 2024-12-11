from contextlib import contextmanager

from django.db import transaction

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import Chat
from apps.experiments.models import ConsentForm, Experiment, ExperimentSession, Participant
from apps.teams.models import Team
from apps.users.models import CustomUser


@contextmanager
def temporary_session(team: Team, user_id: int):
    """A temporary sesssion setup that is rolled back after the context exits."""
    with transaction.atomic():
        user = CustomUser.objects.get(id=user_id)
        consent_form = ConsentForm.objects.get(team=team, is_default=True)
        experiment = Experiment.objects.create(
            team=team, name="Temporary Experiment", owner=user, consent_form=consent_form
        )
        channel = ExperimentChannel.objects.create(
            team=team, name="Temporary Channel", experiment=experiment, platform=ChannelPlatform.WEB
        )
        chat = Chat.objects.create(team=team, name="Temporary Chat")
        participant, _ = Participant.objects.get_or_create(user=user, team=team, platform=ChannelPlatform.WEB)
        experiment_session = ExperimentSession.objects.create(
            team=team,
            experiment=experiment,
            chat=chat,
            experiment_channel=channel,
            participant=participant,
        )
        yield experiment_session
        transaction.set_rollback(True)
