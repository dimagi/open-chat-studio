"""Shared setup for the bot-initiated ("trigger bot") message flow.

Before ``trigger_bot_message_task`` can be dispatched, someone has to resolve the channel, the
participant data and the session the message will be sent in. That used to live only in the API
view, so the participant detail page kept calling the task with the dict argument it took before
the session moved into the caller, and 500'd (#4221). It lives here so both callers share it.

Callers dispatch the task themselves -- they differ in whether they can send ``message_text`` --
and map :class:`TriggerBotMessageError` onto their own error surface (a JSON body for the API, a
form error for the web view).
"""

import httpx
from rest_framework import status

from apps.api.tasks import (
    DuplicateConnectChannelError,
    connect_channel_error_details,
    create_connect_channel_for_participant,
)
from apps.channels.clients.connect_client import CommCareConnectClient
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.registry import get_channel_class_for_platform
from apps.chatbots.version_resolver import resolve_published_or_working
from apps.experiments.models import Experiment, ExperimentSession, Participant, ParticipantData
from apps.teams.models import Team
from apps.teams.utils import current_team


class TriggerBotMessageError(Exception):
    """A bot message cannot be triggered. ``detail`` is safe to show the caller."""

    def __init__(self, detail: str, status_code: int = status.HTTP_400_BAD_REQUEST):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def prepare_trigger_bot_message(
    team: Team,
    experiment: Experiment,
    identifier: str,
    platform: str,
    *,
    start_new_session: bool = False,
    session_data: dict | None = None,
    incoming_participant_data: dict | None = None,
) -> tuple[ExperimentSession, ParticipantData]:
    """Resolve everything ``trigger_bot_message_task`` needs and return the session to send in.

    The session is created synchronously (rather than in the task) so that callers can report it
    back to the user before the task runs. Raises :class:`TriggerBotMessageError` when the message
    cannot be triggered at all: no channel for the platform, a channel an admin has switched off,
    a CommCare Connect channel that could not be created, or a participant who has not consented.
    """
    identifier = ChannelPlatform(platform).normalize_identifier(identifier)
    channel = get_trigger_bot_channel(experiment, platform)
    participant_data = get_or_create_participant_data(team, identifier, platform, experiment, incoming_participant_data)

    if platform == ChannelPlatform.COMMCARE_CONNECT:
        ensure_commcare_connect_ready(channel, identifier, participant_data)

    target_experiment = resolve_published_or_working(experiment)
    ChannelClass = get_channel_class_for_platform(platform)
    bot_channel = ChannelClass(experiment=target_experiment, experiment_channel=channel)
    with current_team(experiment.team):
        bot_channel.ensure_session_exists_for_participant(identifier, new_session=start_new_session)
        session = bot_channel.experiment_session
        assert session is not None
        if session_data:
            session.state = {**session.state, **session_data}
            session.save(update_fields=["state"])

    return session, participant_data


def get_trigger_bot_channel(experiment: Experiment, platform: str) -> ExperimentChannel:
    """Return the channel ``experiment`` may send ``platform`` messages on.

    A channel an admin has switched off must not open a session or push a message out, mirroring
    ``ChannelDisabledStage`` on the inbound path.
    """
    channel = ExperimentChannel.objects.filter(platform=platform, experiment=experiment).first()
    if not channel:
        raise TriggerBotMessageError(
            f"Experiment cannot send messages on the {platform} channel. Create the channel first."
        )
    if channel.is_disabled:
        raise TriggerBotMessageError(f"The {platform} channel for this experiment is disabled.")
    return channel


def get_or_create_participant_data(
    team: Team,
    identifier: str,
    platform: str,
    experiment: Experiment,
    incoming_participant_data: dict | None = None,
) -> ParticipantData:
    """Get or create a participant and their ParticipantData for an experiment.

    If ``incoming_participant_data`` is provided it is merged into any existing data.
    Returns the (possibly newly created) ``ParticipantData`` instance.
    """
    participant_data = ParticipantData.objects.filter(
        participant__identifier=identifier,
        participant__platform=platform,
        experiment=experiment.id,
    ).first()

    if not participant_data:
        participant, _ = Participant.objects.get_or_create(identifier=identifier, platform=platform, team=team)
        participant_data, created = ParticipantData.objects.get_or_create(
            participant=participant,
            experiment=experiment,
            defaults={"team": team, "data": incoming_participant_data or {}},
        )
        if created:
            return participant_data

    if incoming_participant_data:
        merged_data = {**participant_data.data, **incoming_participant_data}
        if merged_data != participant_data.data:
            participant_data.data = merged_data
            participant_data.save(update_fields=["data"])

    return participant_data


def ensure_commcare_connect_ready(channel: ExperimentChannel, identifier: str, participant_data: ParticipantData):
    """Ensure a CommCare Connect channel exists for the participant and that they have consented.

    Raises :class:`TriggerBotMessageError` if the channel cannot be created or consent has not
    been given.
    """
    if not participant_data.system_metadata.get("commcare_connect_channel_id"):
        connect_client = CommCareConnectClient()
        try:
            create_connect_channel_for_participant(channel, connect_client, identifier, participant_data)
        except (DuplicateConnectChannelError, httpx.HTTPError) as e:
            status_code, detail = connect_channel_error_details(e, identifier)
            raise TriggerBotMessageError(detail, status_code) from e

    if not participant_data.has_consented():
        raise TriggerBotMessageError("User has not given consent")
