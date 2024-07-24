import pytest
from django.test import override_settings
from mock.mock import Mock, patch

from apps.channels.models import ChannelPlatform
from apps.chat.channels import WebChannel


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@pytest.mark.parametrize("with_seed_message", [True, False])
@patch("apps.events.tasks.enqueue_static_triggers", Mock())
@patch("apps.chat.channels.WebChannel.new_user_message")
def test_start_new_session(new_user_message, with_seed_message, experiment):
    """A simple test to make sure we create"""
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


# TODO: Add more tests
