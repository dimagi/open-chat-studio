from contextlib import contextmanager
from typing import Self

from django.db import transaction

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import Chat
from apps.experiments.models import ConsentForm, Experiment, ExperimentSession, Participant, ParticipantData
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


class ParticipantDataProxy:
    """Allows multiple access without needing to re-fetch from the DB"""

    @classmethod
    def from_state(cls, pipeline_state) -> Self:
        # using `.get` here for the sake of tests. In practice the session should always be present
        return cls(pipeline_state.get("experiment_session"))

    def __init__(self, experiment_session):
        self.session = experiment_session
        self._participant_data = None

    def _get_db_object(self):
        if not self._participant_data:
            self._participant_data, _ = ParticipantData.objects.get_or_create(
                participant_id=self.session.participant_id,
                experiment_id=self.session.experiment_id,
                team_id=self.session.experiment.team_id,
            )
        return self._participant_data

    def get(self, key, *args, **kwargs):
        data = self._get_db_object().data or {}
        default_value = args[0] if args else None
        return data.get(key, default_value)

    def set(self, data):
        participant_data = self._get_db_object()
        participant_data.data = data
        participant_data.save(update_fields=["data"])
