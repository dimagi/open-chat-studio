import pytest

from apps.annotations.models import UserComment
from apps.chat.models import ChatMessageType
from apps.evaluations.models import EvaluationMessage, ExperimentVersionSelection
from apps.utils.factories.evaluations import EvaluationConfigFactory
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentFactory, ExperimentSessionFactory
from apps.web.dynamic_filters.datastructures import ColumnFilterData, FilterParams


@pytest.mark.django_db()
def test_create_messages_from_sessions_includes_history():
    session_1 = ExperimentSessionFactory()
    session_2 = ExperimentSessionFactory(team=session_1.team)

    # Two message pairs from the first session
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="session1 message1 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="session1 message1 ai", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="session1 message2 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="session1 message2 ai", chat=session_1.chat)

    # One message pair from the second session (with a seed message in the history)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="session2 message0 ai", chat=session_2.chat)
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="session2 message1 human", chat=session_2.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="session2 message1 ai", chat=session_2.chat)

    eval_messages = EvaluationMessage.create_from_sessions(
        session_1.team, [session_1.external_id, session_2.external_id]
    )

    assert len(eval_messages) == 4  # This includes the single "AI seed message"

    assert eval_messages[0].input == {"content": "session1 message1 human", "role": "human"}
    assert eval_messages[0].output == {"content": "session1 message1 ai", "role": "ai"}

    assert eval_messages[1].input == {"content": "session1 message2 human", "role": "human"}
    assert eval_messages[1].output == {"content": "session1 message2 ai", "role": "ai"}

    assert eval_messages[2].input == {}
    assert eval_messages[2].output == {"content": "session2 message0 ai", "role": "ai"}

    assert eval_messages[3].input == {"content": "session2 message1 human", "role": "human"}
    assert eval_messages[3].output == {"content": "session2 message1 ai", "role": "ai"}

    # Test JSON history field
    assert eval_messages[0].history == []
    assert eval_messages[0].full_history == ""

    assert len(eval_messages[1].history) == 2
    assert eval_messages[1].history[0]["message_type"] == ChatMessageType.HUMAN
    assert eval_messages[1].history[0]["content"] == "session1 message1 human"
    assert eval_messages[1].history[1]["message_type"] == ChatMessageType.AI
    assert eval_messages[1].history[1]["content"] == "session1 message1 ai"
    assert eval_messages[1].full_history == "user: session1 message1 human\nassistant: session1 message1 ai"

    # AI seed message from session2 should have empty history
    assert eval_messages[2].history == []
    assert eval_messages[2].full_history == ""

    assert eval_messages[3].history == [
        {"message_type": ChatMessageType.AI, "content": "session2 message0 ai", "summary": None}
    ]
    assert eval_messages[3].full_history == "assistant: session2 message0 ai"


@pytest.mark.django_db()
def test_create_messages_from_sessions_includes_comments(team_with_users):
    session_1 = ExperimentSessionFactory(team=team_with_users)
    user = team_with_users.members.first()

    team = session_1.team
    human_message = ChatMessageFactory(
        message_type=ChatMessageType.HUMAN, content="session1 message1 human", chat=session_1.chat
    )
    UserComment.add_for_model(human_message, comment="comment1", added_by=user, team=team)
    UserComment.add_for_model(human_message, comment="comment2", added_by=user, team=team)

    ai_message = ChatMessageFactory(
        message_type=ChatMessageType.AI, content="session1 message1 ai", chat=session_1.chat
    )
    UserComment.add_for_model(ai_message, comment="comment3", added_by=user, team=team)

    eval_messages = EvaluationMessage.create_from_sessions(team, [session_1.external_id])

    assert len(eval_messages) == 1

    assert eval_messages[0].context["comments"] == ["comment1", "comment2", "comment3"]


@pytest.mark.django_db()
def test_create_messages_from_sessions_includes_tags():
    session_1 = ExperimentSessionFactory()

    team = session_1.team
    human_message = ChatMessageFactory(
        message_type=ChatMessageType.HUMAN, content="session1 message1 human", chat=session_1.chat
    )
    human_message.add_version_tag(2, True)
    human_message.add_rating("+1")

    ai_message = ChatMessageFactory(
        message_type=ChatMessageType.AI, content="session1 message1 ai", chat=session_1.chat
    )
    ai_message.add_version_tag(3, True)
    ai_message.add_rating("+2")

    eval_messages = EvaluationMessage.create_from_sessions(team, [session_1.external_id])

    assert len(eval_messages) == 1

    assert eval_messages[0].context["tags"] == ["+1", "+2"]


@pytest.mark.django_db()
def test_get_generation_experiment_version_specific():
    experiment = ExperimentFactory()
    config = EvaluationConfigFactory(
        experiment_version=experiment,
        base_experiment=experiment,
        version_selection_type=ExperimentVersionSelection.SPECIFIC,
    )
    assert config.get_generation_experiment_version() == experiment


@pytest.mark.django_db()
def test_get_generation_experiment_version_latest_working():
    working_experiment = ExperimentFactory()
    working_experiment.create_new_version("test")

    config = EvaluationConfigFactory(
        experiment_version=None,
        base_experiment=working_experiment,
        version_selection_type=ExperimentVersionSelection.LATEST_WORKING,
        team=working_experiment.team,
    )
    assert config.get_generation_experiment_version() == working_experiment


@pytest.mark.django_db()
def test_get_generation_experiment_version_latest_published():
    working_experiment = ExperimentFactory()
    working_experiment.create_new_version("test", make_default=True)
    published_version = working_experiment.create_new_version("test", make_default=True)

    config = EvaluationConfigFactory(
        experiment_version=None,
        base_experiment=working_experiment,
        version_selection_type=ExperimentVersionSelection.LATEST_PUBLISHED,
        team=working_experiment.team,
    )
    assert config.get_generation_experiment_version() == published_version


@pytest.mark.django_db()
def test_get_generation_experiment_version_latest_published_none():
    """When there are no published versions but we are targeting it, we use the working version"""

    working_experiment = ExperimentFactory()
    config = EvaluationConfigFactory(
        experiment_version=None,
        base_experiment=working_experiment,
        version_selection_type=ExperimentVersionSelection.LATEST_PUBLISHED,
        team=working_experiment.team,
    )
    assert config.get_generation_experiment_version().is_working_version


@pytest.mark.django_db()
def test_get_generation_experiment_version_no_base_experiment():
    config = EvaluationConfigFactory(
        experiment_version=None,
        base_experiment=None,
        version_selection_type=ExperimentVersionSelection.LATEST_WORKING,
    )
    assert config.get_generation_experiment_version() is None


@pytest.mark.django_db()
def test_create_from_sessions_with_filtered_sessions_only():
    """Test cloning only filtered messages from sessions."""
    session_1 = ExperimentSessionFactory()
    team = session_1.team

    # Create messages with different ratings
    human_msg1 = ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message1 human", chat=session_1.chat)
    human_msg1.add_rating("+1")  # This will be filtered
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message1 ai", chat=session_1.chat)

    human_msg2 = ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message2 human", chat=session_1.chat)
    human_msg2.add_rating("-1")  # This will be filtered out
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message2 ai", chat=session_1.chat)

    # Create filter params to only include +1 rated messages
    filter_params = FilterParams(column_filters=[ColumnFilterData(column="tags", operator="any_of", value='["+1"]')])

    eval_messages = EvaluationMessage.create_from_sessions(
        team=team,
        external_session_ids=[],
        filtered_session_ids=[session_1.external_id],
        filter_params=filter_params,
        timezone=None,
    )

    # Should only get the message pair with +1 rating
    assert len(eval_messages) == 1
    assert eval_messages[0].input == {"content": "message1 human", "role": "human"}
    assert eval_messages[0].output == {"content": "message1 ai", "role": "ai"}


@pytest.mark.django_db()
def test_create_from_sessions_mixed_regular_and_filtered():
    """Test combining regular sessions (all messages) with filtered sessions."""
    session_1 = ExperimentSessionFactory()
    session_2 = ExperimentSessionFactory(team=session_1.team)
    team = session_1.team

    # Session 1: Regular session - all messages should be included
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="session1 message1 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="session1 message1 ai", chat=session_1.chat)

    # Session 2: Filtered session - only +1 rated messages should be included
    human_msg1 = ChatMessageFactory(
        message_type=ChatMessageType.HUMAN, content="session2 message1 human", chat=session_2.chat
    )
    human_msg1.add_rating("+1")  # This will be included
    ChatMessageFactory(
        message_type=ChatMessageType.AI, content="session2 message1 ai", chat=session_2.chat
    )  # This will also be included
    ChatMessageFactory(
        message_type=ChatMessageType.HUMAN, content="session2 message1 human", chat=session_2.chat
    )  # This won't be included

    human_msg3 = ChatMessageFactory(
        message_type=ChatMessageType.HUMAN, content="session2 message2 human", chat=session_2.chat
    )
    human_msg3.add_rating("-1")  # This won't be incldued
    ChatMessageFactory(message_type=ChatMessageType.AI, content="session2 message2 ai", chat=session_2.chat)

    # Create filter params to only include +1 rated messages
    filter_params = FilterParams(column_filters=[ColumnFilterData(column="tags", operator="any_of", value='["+1"]')])

    eval_messages = EvaluationMessage.create_from_sessions(
        team=team,
        external_session_ids=[session_1.external_id],
        filtered_session_ids=[session_2.external_id],
        filter_params=filter_params,
        timezone=None,
    )

    # Should get: 1 from session_1 (regular) + 1 from session_2 (filtered)
    assert len(eval_messages) == 2

    eval_messages.sort(key=lambda msg: msg.input_chat_message.id)

    assert eval_messages[0].input == {"content": "session1 message1 human", "role": "human"}
    assert eval_messages[0].output == {"content": "session1 message1 ai", "role": "ai"}

    assert eval_messages[1].input == {"content": "session2 message1 human", "role": "human"}
    assert eval_messages[1].output == {"content": "session2 message1 ai", "role": "ai"}


@pytest.mark.django_db()
def test_create_from_sessions_no_filter_params():
    """Test that filtered sessions are ignored when no filter params provided."""
    session_1 = ExperimentSessionFactory()
    team = session_1.team

    # Create messages
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message1 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message1 ai", chat=session_1.chat)

    eval_messages = EvaluationMessage.create_from_sessions(
        team=team,
        external_session_ids=[],
        filtered_session_ids=[session_1.external_id],
        filter_params=None,  # No filter params
        timezone=None,
    )

    # Should get no messages since filtered sessions are ignored without filter params
    assert len(eval_messages) == 0


@pytest.mark.django_db()
def test_create_from_sessions_empty_sessions():
    """Test behavior with empty session lists."""
    session_1 = ExperimentSessionFactory()
    team = session_1.team

    eval_messages = EvaluationMessage.create_from_sessions(
        team=team, external_session_ids=[], filtered_session_ids=[], filter_params=None, timezone=None
    )

    assert len(eval_messages) == 0


@pytest.mark.django_db()
def test_filtered_messages_include_complete_history():
    """Test that filtered messages include complete session history, not just filtered messages."""
    session_1 = ExperimentSessionFactory()
    team = session_1.team

    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message1 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message1 ai", chat=session_1.chat)

    human_msg2 = ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message2 human", chat=session_1.chat)
    human_msg2.add_rating("+1")
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message2 ai", chat=session_1.chat)

    human_msg3 = ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message3 human", chat=session_1.chat)
    human_msg3.add_rating("+1")
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message3 ai", chat=session_1.chat)

    filter_params = FilterParams(column_filters=[ColumnFilterData(column="tags", operator="any_of", value='["+1"]')])

    eval_messages = EvaluationMessage.create_from_sessions(
        team=team,
        external_session_ids=[],
        filtered_session_ids=[session_1.external_id],
        filter_params=filter_params,
        timezone=None,
    )

    # Should only get one evaluation message (the filtered one)
    assert len(eval_messages) == 2
    assert eval_messages[0].input == {"content": "message2 human", "role": "human"}
    assert eval_messages[0].output == {"content": "message2 ai", "role": "ai"}
    assert eval_messages[1].input == {"content": "message3 human", "role": "human"}
    assert eval_messages[1].output == {"content": "message3 ai", "role": "ai"}

    assert len(eval_messages[0].history) == 2
    assert eval_messages[0].history[0]["content"] == "message1 human"
    assert eval_messages[0].history[1]["content"] == "message1 ai"


@pytest.mark.django_db()
def test_consecutive_human_messages():
    """Test that consecutive HUMAN messages each create an evaluation message with null AI output.
    This could happen there AI message fails to generate.
    """
    session_1 = ExperimentSessionFactory()
    team = session_1.team

    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message1 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message1 ai", chat=session_1.chat)

    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message2 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message3 human", chat=session_1.chat)

    eval_messages = EvaluationMessage.create_from_sessions(
        team=team,
        external_session_ids=[session_1.external_id],
        timezone=None,
    )

    assert len(eval_messages) == 3

    assert eval_messages[0].input == {"content": "message1 human", "role": "human"}
    assert eval_messages[0].output == {"content": "message1 ai", "role": "ai"}

    assert eval_messages[1].input == {"content": "message2 human", "role": "human"}
    assert eval_messages[1].expected_output_chat_message is None
    assert eval_messages[1].output == {}

    assert eval_messages[2].input == {"content": "message3 human", "role": "human"}
    assert eval_messages[2].expected_output_chat_message is None
    assert eval_messages[2].output == {}


@pytest.mark.django_db()
def test_consecutive_ai_messages():
    """Test that consecutive AI messages
    This can happen when a scheduled message is sent
    """
    session_1 = ExperimentSessionFactory()
    team = session_1.team

    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message1 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message1 ai", chat=session_1.chat)

    # Create two consecutive AI messages
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message2 ai", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message3 ai", chat=session_1.chat)

    eval_messages = EvaluationMessage.create_from_sessions(
        team=team,
        external_session_ids=[session_1.external_id],
        timezone=None,
    )

    assert len(eval_messages) == 3

    assert eval_messages[0].input == {"content": "message1 human", "role": "human"}
    assert eval_messages[0].output == {"content": "message1 ai", "role": "ai"}

    assert eval_messages[1].input_chat_message is None
    assert eval_messages[1].input == {}
    assert eval_messages[1].output == {"content": "message2 ai", "role": "ai"}

    assert eval_messages[2].input_chat_message is None
    assert eval_messages[2].input == {}
    assert eval_messages[2].output == {"content": "message3 ai", "role": "ai"}


@pytest.mark.django_db()
def test_filtered_messages_complete_history_with_mixed_pairs():
    """Test that history includes all messages chronologically, even when filtering creates gaps."""
    session_1 = ExperimentSessionFactory()
    team = session_1.team

    # Pair 1: Normal pair (not tagged)
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message1 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message1 ai", chat=session_1.chat)

    # Unpaired HUMAN (not tagged)
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message2 human unpaired", chat=session_1.chat)

    # Pair 2: Normal pair (not tagged)
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message3 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message3 ai", chat=session_1.chat)

    # Unpaired AI (not tagged)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message4 ai unpaired", chat=session_1.chat)

    # Pair 3: Normal pair (TAGGED - this should be in results)
    human_msg5 = ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message5 human", chat=session_1.chat)
    human_msg5.add_rating("+1")
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message5 ai", chat=session_1.chat)

    # Filter to only include the tagged message
    filter_params = FilterParams(column_filters=[ColumnFilterData(column="tags", operator="any_of", value='["+1"]')])

    eval_messages = EvaluationMessage.create_from_sessions(
        team=team,
        external_session_ids=[],
        filtered_session_ids=[session_1.external_id],
        filter_params=filter_params,
        timezone=None,
    )

    # Should get 1 evaluation message for the tagged pair
    assert len(eval_messages) == 1
    assert eval_messages[0].input == {"content": "message5 human", "role": "human"}
    assert eval_messages[0].output == {"content": "message5 ai", "role": "ai"}

    # History should contain ALL previous messages (6 messages total before this pair)
    # Including the unpaired messages
    assert len(eval_messages[0].history) == 6

    # Verify history order and content
    assert eval_messages[0].history[0]["content"] == "message1 human"
    assert eval_messages[0].history[0]["message_type"] == ChatMessageType.HUMAN

    assert eval_messages[0].history[1]["content"] == "message1 ai"
    assert eval_messages[0].history[1]["message_type"] == ChatMessageType.AI

    assert eval_messages[0].history[2]["content"] == "message2 human unpaired"
    assert eval_messages[0].history[2]["message_type"] == ChatMessageType.HUMAN

    assert eval_messages[0].history[3]["content"] == "message3 human"
    assert eval_messages[0].history[3]["message_type"] == ChatMessageType.HUMAN

    assert eval_messages[0].history[4]["content"] == "message3 ai"
    assert eval_messages[0].history[4]["message_type"] == ChatMessageType.AI

    assert eval_messages[0].history[5]["content"] == "message4 ai unpaired"
    assert eval_messages[0].history[5]["message_type"] == ChatMessageType.AI
