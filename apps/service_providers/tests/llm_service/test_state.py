import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.service_providers.llm_service.state import (
    ChatExperimentState,
    ExperimentAssistantState,
    PipelineAssistantState,
)
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@pytest.mark.django_db()
class TestChatExperimentState:
    def test_save_message_to_history_stores_ai_message_on_state(self, session):
        state = ChatExperimentState(session=session, experiment=session.experiment)
        state.save_message_to_history(message="hi", type_=ChatMessageType.HUMAN)
        assert state.ai_message is None
        state.save_message_to_history(message="hi human", type_=ChatMessageType.AI)
        assert state.ai_message == ChatMessage.objects.get(message_type=ChatMessageType.AI)


@pytest.mark.django_db()
class TestExperimentAssistantState:
    def test_save_message_to_history_stores_ai_message_on_state(self, session):
        state = ExperimentAssistantState(session=session, experiment=session.experiment)
        state.save_message_to_history(message="hi", type_=ChatMessageType.HUMAN)
        assert state.ai_message is None
        state.save_message_to_history(message="hi human", type_=ChatMessageType.AI)
        assert state.ai_message == ChatMessage.objects.get(message_type=ChatMessageType.AI)


@pytest.mark.django_db()
class TestAssistantStateSubclasses:
    """
    Behavioral testing for the subclasses of `BaseAssistantState`. The other methods are either self-sustaining or
    depend on the return values of these methods. So, testing these methods should be enough to cover the differences
    between the subclasses. Note that this excludes the implementation of the `BaseRunnableState` abstract methods.
    """

    def init_state(self, state_cls, **state_kwargs):
        session = state_kwargs.get("session")
        trace_service = state_kwargs.get("trace_service")
        if state_cls == ExperimentAssistantState:
            experiment = session.experiment
            return ExperimentAssistantState(session=session, experiment=experiment, trace_service=trace_service)
        elif state_cls == PipelineAssistantState:
            return PipelineAssistantState(
                session=session,
                assistant=state_kwargs.get("assistant"),
                trace_service=trace_service,
                input_formatter=state_kwargs.get("input_formatter", ""),
                citations_enabled=state_kwargs.get("citations_enabled", True),
            )

    @pytest.mark.parametrize("state_cls", [ExperimentAssistantState, PipelineAssistantState])
    def test_pre_run_hook(self, state_cls):
        """
        This hook creates a message in the case of `ExperimentAssistantState`, but only saves the metadata on the
        instance in the case of `PipelineAssistantState`.
        """
        session = ExperimentSessionFactory()
        assistant = OpenAiAssistantFactory()
        state = self.init_state(state_cls=state_cls, session=session, assistant=assistant)

        kwargs = {
            "input": "hi",
            "config": {"configurable": {"save_input_to_history": True}},
            "message_metadata": {"key1": 1},
        }
        state.pre_run_hook(**kwargs)

        if state_cls == ExperimentAssistantState:
            assert ChatMessage.objects.filter(
                message_type=ChatMessageType.HUMAN, content="hi", metadata={"key1": 1}
            ).exists()
        elif state_cls == PipelineAssistantState:
            assert state.input_message_metadata == {"key1": 1}
            assert state.output_message_metadata == {}

    @pytest.mark.parametrize("state_cls", [ExperimentAssistantState, PipelineAssistantState])
    def test_post_run_hook(self, state_cls):
        """
        This hook creates a message in the case of `ExperimentAssistantState`, but only saves the metadata on the
        instance in the case of `PipelineAssistantState`.
        """
        session = ExperimentSessionFactory()
        assistant = OpenAiAssistantFactory()
        state = self.init_state(state_cls=state_cls, session=session, assistant=assistant)

        kwargs = {
            "output": "hi",
            "config": {"configurable": {"experiment_tag": "tester"}},
            "message_metadata": {"key1": 1},
        }
        state.post_run_hook(**kwargs)

        if state_cls == ExperimentAssistantState:
            message = ChatMessage.objects.get(message_type=ChatMessageType.AI, content="hi", metadata={"key1": 1})
            assert message.tags.first().name == "tester"
        elif state_cls == PipelineAssistantState:
            assert state.output_message_metadata == {"key1": 1}
            assert state.input_message_metadata == {}

    @pytest.mark.parametrize(
        ("state_cls", "citations_enabled"),
        [
            (ExperimentAssistantState, True),
            (ExperimentAssistantState, False),
            (PipelineAssistantState, True),
            (PipelineAssistantState, False),
        ],
    )
    def test_citations_enabled(self, state_cls, citations_enabled):
        session = ExperimentSessionFactory(experiment__citations_enabled=citations_enabled)
        assistant = OpenAiAssistantFactory()
        state = self.init_state(
            state_cls=state_cls, session=session, assistant=assistant, citations_enabled=citations_enabled
        )

        assert state.citations_enabled == citations_enabled

    @pytest.mark.parametrize("state_cls", [ExperimentAssistantState, PipelineAssistantState])
    def test_assistant_property(self, state_cls):
        assistant = OpenAiAssistantFactory()
        session = ExperimentSessionFactory(experiment__assistant=assistant)
        state = self.init_state(state_cls=state_cls, session=session, assistant=assistant)

        assert state.assistant == assistant

    @pytest.mark.parametrize("state_cls", [ExperimentAssistantState, PipelineAssistantState])
    def test_chat_property(self, state_cls):
        assistant = OpenAiAssistantFactory()
        session = ExperimentSessionFactory()
        state = self.init_state(state_cls=state_cls, session=session, assistant=assistant)

        assert state.chat == session.chat


# TODO: Test subclass implementations of BaseRunnableState's abstract methods
