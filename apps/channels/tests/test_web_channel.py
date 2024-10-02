import pytest
from django.test import override_settings
from mock.mock import Mock, patch

from apps.channels.models import ChannelPlatform
from apps.chat.channels import WebChannel
from apps.chat.models import Chat


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@pytest.mark.parametrize("with_seed_message", [True, False])
@patch("apps.events.tasks.enqueue_static_triggers", Mock())
@patch("apps.chat.channels.WebChannel.new_user_message")
def test_start_new_session(new_user_message, with_seed_message, experiment):
    """A simple test to make sure we create a session and send a session message"""
    if with_seed_message:
        experiment.seed_message = "Tell a joke"
        experiment.save()

    session = WebChannel.start_new_session(
        experiment,
        "jack@titanic.com",
    )

    assert session is not None
    assert session.participant.identifier == "jack@titanic.com"
    assert session.experiment_channel is not None
    assert session.experiment_channel.platform == ChannelPlatform.WEB

    if with_seed_message:
        assert session.seed_task_id is not None
        new_user_message.assert_called()
        message = new_user_message.call_args[0][0]
        assert message.participant_id == "jack@titanic.com"
        assert message.message_text == "Tell a joke"
        # A seed message cannot have an attachment
        assert message.attachments == []


@pytest.mark.django_db()
class TestVersioning:
    @patch("apps.events.tasks.enqueue_static_triggers", Mock())
    @patch("apps.chat.channels.WebChannel.check_and_process_seed_message")
    def test_start_new_session_uses_default_version(self, check_and_process_seed_message, experiment):
        new_version = experiment.create_new_version()
        session = WebChannel.start_new_session(
            experiment,
            "jack@titanic.com",
        )

        _session_used, experiment_used = check_and_process_seed_message.call_args[0]
        assert experiment_used == new_version
        assert session.experiment == experiment
        assert session.chat.metadata.get(Chat.MetadataKeys.EXPERIMENT_VERSION) == "default"

    @patch("apps.events.tasks.enqueue_static_triggers", Mock())
    @patch("apps.chat.channels.WebChannel.check_and_process_seed_message")
    def test_start_new_session_uses_specified_version(self, check_and_process_seed_message, experiment):
        new_version = experiment.create_new_version()
        session = WebChannel.start_new_session(experiment, "jack@titanic.com", version=1)

        _session_used, experiment_used = check_and_process_seed_message.call_args[0]
        assert experiment_used == new_version
        assert session.experiment == experiment
        assert session.chat.metadata.get(Chat.MetadataKeys.EXPERIMENT_VERSION) == 1
