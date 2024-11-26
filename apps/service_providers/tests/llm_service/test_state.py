import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.service_providers.llm_service.state import (
    ExperimentAdapter,
    PipelineAdapter,
)
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@pytest.mark.django_db()
class TestExperimentAdapter:
    def test_save_message_to_history_stores_ai_message_on_state(self, session):
        state = ExperimentAdapter(session=session, experiment=session.experiment)
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
        if state_cls == ExperimentAdapter:
            experiment = session.experiment
            return ExperimentAdapter(session=session, experiment=experiment, trace_service=trace_service)
        elif state_cls == PipelineAdapter:
            return PipelineAdapter(
                session=session,
                assistant=state_kwargs.get("assistant"),
                trace_service=trace_service,
                input_formatter=state_kwargs.get("input_formatter", ""),
                citations_enabled=state_kwargs.get("citations_enabled", True),
            )

    @pytest.mark.parametrize("state_cls", [ExperimentAdapter, PipelineAdapter])
    def test_pre_run_hook(self, state_cls):
        """
        This hook creates a message in the case of `ExperimentAdapter`, but only saves the metadata on the
        instance in the case of `PipelineAdapter`.
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

        if state_cls == ExperimentAdapter:
            assert ChatMessage.objects.filter(
                message_type=ChatMessageType.HUMAN, content="hi", metadata={"key1": 1}
            ).exists()
        elif state_cls == PipelineAdapter:
            assert state.input_message_metadata == {"key1": 1}
            assert state.output_message_metadata == {}

    @pytest.mark.parametrize("state_cls", [ExperimentAdapter, PipelineAdapter])
    def test_post_run_hook(self, state_cls):
        """
        This hook creates a message in the case of `ExperimentAdapter`, but only saves the metadata on the
        instance in the case of `PipelineAdapter`.
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

        if state_cls == ExperimentAdapter:
            message = ChatMessage.objects.get(message_type=ChatMessageType.AI, content="hi", metadata={"key1": 1})
            assert message.tags.first().name == "tester"
        elif state_cls == PipelineAdapter:
            assert state.output_message_metadata == {"key1": 1}
            assert state.input_message_metadata == {}

    @pytest.mark.parametrize(
        ("state_cls", "citations_enabled"),
        [
            (ExperimentAdapter, True),
            (ExperimentAdapter, False),
            (PipelineAdapter, True),
            (PipelineAdapter, False),
        ],
    )
    def test_citations_enabled(self, state_cls, citations_enabled):
        session = ExperimentSessionFactory(experiment__citations_enabled=citations_enabled)
        assistant = OpenAiAssistantFactory()
        state = self.init_state(
            state_cls=state_cls, session=session, assistant=assistant, citations_enabled=citations_enabled
        )

        assert state.citations_enabled == citations_enabled

    @pytest.mark.parametrize("state_cls", [ExperimentAdapter, PipelineAdapter])
    def test_assistant_property(self, state_cls):
        assistant = OpenAiAssistantFactory()
        session = ExperimentSessionFactory(experiment__assistant=assistant)
        state = self.init_state(state_cls=state_cls, session=session, assistant=assistant)

        assert state.assistant == assistant

    @pytest.mark.parametrize("state_cls", [ExperimentAdapter, PipelineAdapter])
    def test_chat_property(self, state_cls):
        assistant = OpenAiAssistantFactory()
        session = ExperimentSessionFactory()
        state = self.init_state(state_cls=state_cls, session=session, assistant=assistant)

        assert state.chat == session.chat

    @pytest.mark.parametrize("state_cls", [PipelineAdapter, ExperimentAdapter])
    def test_get_llm_service(self, state_cls):
        llm_provider = LlmProviderFactory()
        assistant = OpenAiAssistantFactory(llm_provider=llm_provider)
        session = ExperimentSessionFactory(experiment__llm_provider=llm_provider)
        state = self.init_state(state_cls=state_cls, session=session, assistant=assistant)

        state.get_llm_service() == llm_provider

    @pytest.mark.parametrize("state_cls", [PipelineAdapter, ExperimentAdapter])
    def test_callback_handler(self, state_cls):
        llm_provider_model = LlmProviderModelFactory()
        llm_provider = LlmProviderFactory()
        assistant = OpenAiAssistantFactory(llm_provider=llm_provider, llm_provider_model=llm_provider_model)
        session = ExperimentSessionFactory(
            experiment__llm_provider=llm_provider, experiment__llm_provider_model=llm_provider_model
        )
        state = self.init_state(state_cls=state_cls, session=session, assistant=assistant)

        state.callback_handler == llm_provider.get_llm_service().get_callback_handler(llm_provider_model.name)

    @pytest.mark.parametrize("state_cls", [PipelineAdapter, ExperimentAdapter])
    def test_format_input(self, state_cls):
        input_formatter = "message: {input}"
        assistant = OpenAiAssistantFactory()
        session = ExperimentSessionFactory(experiment__input_formatter=input_formatter)
        state = self.init_state(
            state_cls=state_cls, session=session, assistant=assistant, input_formatter=input_formatter
        )

        state.format_input("Hi there") == "message: Hi there"
