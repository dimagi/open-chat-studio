import pytest
from django.test import override_settings

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.events.models import (
    EventAction,
    EventActionType,
    StaticTrigger,
    StaticTriggerType,
)
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
)
from apps.utils.factories.pipelines import PipelineFactory


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@pytest.fixture()
def pipeline():
    return PipelineFactory()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@pytest.mark.django_db()
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
            action_type=EventActionType.PIPELINE_START, params={"pipeline_id": pipeline.id}
        ),
        type=StaticTriggerType.CONVERSATION_END,
    )
    session.end()
    assert static_trigger.event_logs.count() == 1
    log = static_trigger.event_logs.first()
    assert log.status == "success"
    assert log.log == str(
        {
            "messages": [
                input,  # output of pipeline
                input,  # output of first node / input to the second node
                input,  # input to pipeline
            ]
        }
    )
