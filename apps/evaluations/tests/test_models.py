import pytest

from apps.chat.models import ChatMessageType
from apps.evaluations.models import EvaluationMessage, ExperimentVersionSelection
from apps.utils.factories.evaluations import EvaluationConfigFactory
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentFactory, ExperimentSessionFactory


@pytest.mark.django_db()
def test_create_messages_from_sessions_includes_history():
    session_1 = ExperimentSessionFactory()
    session_2 = ExperimentSessionFactory(team=session_1.team)

    # Two message pairs from the first session
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="session1 message1 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="session1 message1 ai", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="session1 message2 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="session1 message2 ai", chat=session_1.chat)

    # One message pair from the second session
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="session2 message1 human", chat=session_2.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="session2 message1 ai", chat=session_2.chat)

    eval_messages = EvaluationMessage.create_from_sessions(
        session_1.team, [session_1.external_id, session_2.external_id]
    )

    assert len(eval_messages) == 3

    assert eval_messages[0].input == {"content": "session1 message1 human", "role": "human"}
    assert eval_messages[0].output == {"content": "session1 message1 ai", "role": "ai"}

    assert eval_messages[1].input == {"content": "session1 message2 human", "role": "human"}
    assert eval_messages[1].output == {"content": "session1 message2 ai", "role": "ai"}

    assert eval_messages[2].input == {"content": "session2 message1 human", "role": "human"}
    assert eval_messages[2].output == {"content": "session2 message1 ai", "role": "ai"}

    # Test JSON history field
    assert eval_messages[0].history == []
    assert eval_messages[0].full_history == ""

    assert len(eval_messages[1].history) == 2
    assert eval_messages[1].history[0]["message_type"] == ChatMessageType.HUMAN
    assert eval_messages[1].history[0]["content"] == "session1 message1 human"
    assert eval_messages[1].history[1]["message_type"] == ChatMessageType.AI
    assert eval_messages[1].history[1]["content"] == "session1 message1 ai"
    assert eval_messages[1].full_history == "human: session1 message1 human\nai: session1 message1 ai"

    assert eval_messages[2].history == []
    assert eval_messages[2].full_history == ""


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
