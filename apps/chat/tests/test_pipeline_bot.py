from unittest import mock

from apps.chat.bots import PipelineBot
from apps.pipelines.nodes.base import PipelineState


def test_session_tags():
    session = mock.Mock()
    bot = PipelineBot(session, mock.Mock(), None)
    bot._save_message_to_history = mock.Mock()
    bot._save_outputs(
        input_state=PipelineState(messages=["hi"]),
        output=PipelineState(messages=["Hello"], session_tags=[("my-tag", None)]),
        save_input_to_history=False,
    )
    session.chat.create_and_add_tag.assert_called_with("my-tag", session.team, tag_category=None)


def test_save_session_state():
    session = mock.Mock()
    bot = PipelineBot(session, mock.Mock(), None)
    bot._save_message_to_history = mock.Mock()
    bot._save_outputs(
        input_state=PipelineState(messages=["hi"]),
        output=PipelineState(messages=["Hello"], session_state={"test": "demo"}),
        save_input_to_history=False,
    )
    assert session.state == {"test": "demo"}
    session.save.assert_called()


def test_save_participant_data():
    session = mock.Mock()
    bot = PipelineBot(session, mock.Mock(), None)
    participant_data = mock.Mock()
    bot._save_message_to_history = mock.Mock()
    bot.__dict__["participant_data"] = participant_data
    bot._save_outputs(
        input_state=PipelineState(messages=["hi"]),
        output=PipelineState(messages=["Hello"], participant_data={"test": "demo"}),
        save_input_to_history=False,
    )
    assert participant_data.data == {"test": "demo"}
    participant_data.save.assert_called()
