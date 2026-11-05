"""Shared helpers for WhatsApp attachment tests (image and document)."""

from unittest.mock import MagicMock

from apps.chat.models import Chat, ChatMessage
from apps.experiments.models import ExperimentSession
from apps.files.models import File
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory

from .channels.conftest import make_context


def make_stage_context(message, provider, get_inbound_media_return):
    """Build a real experiment/session context with a mocked messaging service.

    Returns (ctx, mock_service) so callers can also assert on service interactions.
    """
    experiment = ExperimentFactory(team=provider.team)
    session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
    mock_service = MagicMock()
    mock_service.get_inbound_media.return_value = get_inbound_media_return
    ctx = make_context(message=message, experiment=experiment, experiment_session=session)
    ctx.experiment_channel.messaging_provider.get_messaging_service.return_value = mock_service
    return ctx, mock_service


def setup_inbound_bot_response(experiment, response_text="Got it"):
    """Prep a chat + bot return so handle_*_message proceeds past the bot interaction stage."""
    chat = Chat.objects.create(team=experiment.team)
    return ChatMessage.objects.create(content=response_text, chat=chat)


def assert_file_resolves_via_download_file_join(file: File, team_slug: str) -> None:
    """Assert the File can be reached via the exact ORM join the experiments:download_file
    view uses (File → ChatAttachment → Chat → ExperimentSession). Regression guard for
    the ChatAttachment linkage fix — if linkage is skipped, the join returns nothing and
    the view 404s on download."""
    sessions = ExperimentSession.objects.filter(chat__attachments__files__id=file.id)
    assert sessions.exists(), "File not linked to any ExperimentSession via ChatAttachment"
    session_id = sessions.first().id
    resolved = File.objects.filter(
        id=file.id,
        team__slug=team_slug,
        chatattachment__chat__experiment_session__id=session_id,
    )
    assert resolved.exists(), "download_file view's join would 404 — ChatAttachment linkage missing"
