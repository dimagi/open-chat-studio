import pytest
from django.test import override_settings

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.events.models import (
    EventAction,
    EventActionType,
    StaticTrigger,
    StaticTriggerType,
)
from apps.pipelines.models import PipelineEventInputs
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
)
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.pytest import django_db_transactional


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@pytest.fixture()
def pipeline():
    return PipelineFactory()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@django_db_transactional()
def test_end_conversation_runs_pipeline(session, pipeline):
    input = "Does anything get lost going through the pipe?"
    chat = Chat.objects.create(team=session.team)
    message = ChatMessage.objects.create(
        chat=chat,
        content=input,
        message_type=ChatMessageType.HUMAN,
    )
    message.save()
    session.chat = chat
    session.save()
    static_trigger = StaticTrigger.objects.create(
        experiment=session.experiment,
        action=EventAction.objects.create(
            action_type=EventActionType.PIPELINE_START,
            params={"pipeline_id": pipeline.id, "input_type": PipelineEventInputs.LAST_MESSAGE},
        ),
        type=StaticTriggerType.CONVERSATION_ENDED_BY_BOT,
    )
    session.end(trigger_type=StaticTriggerType.CONVERSATION_ENDED_BY_BOT)
    assert static_trigger.event_logs.count() == 1
    log = static_trigger.event_logs.first()
    assert log.status == "success"

    output_message = f"human: {input}"
    assert log.log == str({
        "messages": [
            output_message,  # output of pipeline
            output_message,  # output of first node / input to the second node
            output_message,  # input to pipeline
        ],
        "outputs": {
            "start": {"message": f"human: {input}", "node_id": "start"},
            "end": {"message": f"human: {input}", "node_id": "end"},
        },
        "experiment_session": session.id,
        "temp_state": {
            "user_input": output_message,
            "attachments": [],
            "outputs": {"start": output_message, "end": output_message},
        },
        "input_message_metadata": {},
        "output_message_metadata": {},
        "output_message_tags": [],
        "session_tags": [],
        "path": [(None, "start", ["end"]), ("start", "end", [])],
        "intents": [],
        "participant_data": session.participant.global_data,
        "session_state": {},
    })
