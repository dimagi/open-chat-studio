import pytest

from apps.chat.models import ChatMessageType
from apps.pipelines.nodes.nodes import AssistantNode
from apps.service_providers.llm_service.adapters import AssistantAdapter, ChatAdapter
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@pytest.mark.django_db()
class TestChatAdapter:
    def test_save_message_to_history_stores_ai_message_on_adapter(self, session):
        adapter = ChatAdapter.for_experiment(session=session, experiment=session.experiment)
        adapter.save_message_to_history(message="hi", type_=ChatMessageType.HUMAN)
        assert adapter.ai_message is None
        adapter.save_message_to_history(message="hi human", type_=ChatMessageType.AI)
        ai_message = adapter.session.chat.messages.filter(message_type=ChatMessageType.AI).first()
        assert adapter.ai_message == ai_message


@pytest.mark.django_db()
class TestAssistantAdapterWithExperiment:
    def _get_adapter(self, config_source):
        session = ExperimentSessionFactory()
        assistant = OpenAiAssistantFactory()
        if config_source == "experiment":
            session.experiment.assistant = assistant
            session.experiment.save()
            return AssistantAdapter.for_experiment(session=session, experiment=session.experiment)
        elif config_source == "pipeline":
            node = AssistantNode(
                assistant_id=assistant.id, citations_enabled=True, input_formatter="here it is: {input}"
            )
            return AssistantAdapter.for_pipeline(session=session, node=node)

    @pytest.mark.parametrize("config_source", ["pipeline", "experiment"])
    def test_pre_run_hook(self, config_source):
        adapter = self._get_adapter(config_source)
        adapter.pre_run_hook(
            input="hi there",
            save_input_to_history=True,
            message_metadata={"key": "value"},
        )
        human_message = adapter.session.chat.messages.filter(message_type=ChatMessageType.HUMAN).first()
        if config_source == "experiment":
            assert human_message.metadata == {"key": "value"}
        elif config_source == "pipeline":
            assert adapter.input_message_metadata == {"key": "value"}

    @pytest.mark.parametrize("config_source", ["pipeline", "experiment"])
    def test_post_run_hook(self, config_source):
        adapter = self._get_adapter(config_source)
        adapter.post_run_hook(
            output="hi human", save_output_to_history=True, message_metadata={"key": "value"}, experiment_tag=None
        )
        ai_message = adapter.session.chat.messages.filter(message_type=ChatMessageType.AI).first()
        if config_source == "experiment":
            assert ai_message.metadata == {"key": "value"}
        elif config_source == "pipeline":
            assert adapter.output_message_metadata == {"key": "value"}
