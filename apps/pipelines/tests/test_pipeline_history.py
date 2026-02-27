from unittest import mock

import pytest

from apps.chat.bots import PipelineBot
from apps.chat.models import ChatMessage, ChatMessageType
from apps.pipelines.models import PipelineChatHistory
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.repository import ORMRepository
from apps.pipelines.tests.utils import create_runnable, end_node, llm_response_with_prompt_node, start_node
from apps.service_providers.tracing import TracingService
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
)
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.langchain import (
    FakeLlmEcho,
    build_fake_llm_service,
)
from apps.utils.pytest import django_db_with_data


@pytest.fixture()
def provider():
    return LlmProviderFactory()


@pytest.fixture()
def pipeline():
    return PipelineFactory()


@pytest.fixture()
def provider_model():
    return LlmProviderModelFactory()


@pytest.fixture()
def experiment_session():
    return ExperimentSessionFactory()


@django_db_with_data()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_llm_with_node_history(get_llm_service, provider, pipeline, experiment_session, provider_model):
    llm = FakeLlmEcho()
    service = build_fake_llm_service(None, [0], llm)
    get_llm_service.return_value = service
    llm_1 = llm_response_with_prompt_node(
        str(provider.id),
        str(provider_model.id),
        prompt="Node 1:",
        history_type="node",
    )
    llm_2 = llm_response_with_prompt_node(
        str(provider.id), str(provider_model.id), prompt="Node 2:", history_type=None
    )  # No history_type
    nodes = [
        start_node(),
        llm_1,
        llm_2,
        end_node(),
    ]
    runnable = create_runnable(pipeline, nodes)

    user_input = "The User Input"
    repo = ORMRepository()
    config = {"configurable": {"repo": repo}}
    runnable.invoke(PipelineState(messages=[user_input], experiment_session=experiment_session), config=config)[
        "messages"
    ]
    expected_call_messages = [
        [("system", "Node 1:"), ("human", user_input)],
        [("system", "Node 2:"), ("human", f"Node 1: {user_input}")],
    ]
    assert [
        [(message.type, message.text()) for message in call] for call in llm.get_call_messages()
    ] == expected_call_messages

    history = PipelineChatHistory.objects.get(session=experiment_session.id, name=llm_1["id"])
    assert history.type == "node"
    assert history.messages.count() == 1
    assert history.messages.first().as_tuples() == [("human", user_input), ("ai", f"Node 1: {user_input}")]

    assert not PipelineChatHistory.objects.filter(session=experiment_session.id, name=llm_2["id"]).exists()

    user_input_2 = "Saying more stuff"
    output_2 = runnable.invoke(
        PipelineState(messages=[user_input_2], experiment_session=experiment_session), config=config
    )["messages"][-1]

    expected_output = f"Node 2: Node 1: {user_input_2}"
    assert output_2 == expected_output

    assert history.messages.count() == 2
    new_messages = history.messages.last().as_tuples()
    assert new_messages == [("human", user_input_2), ("ai", f"Node 1: {user_input_2}")]

    history_2 = PipelineChatHistory.objects.filter(session=experiment_session.id, name=llm_2["id"]).count()
    assert history_2 == 0

    expected_call_messages = [
        [("system", "Node 1:"), ("human", user_input)],
        [("system", "Node 2:"), ("human", f"Node 1: {user_input}")],
        [
            ("system", "Node 1:"),
            ("human", user_input),
            ("ai", f"Node 1: {user_input}"),
            ("human", user_input_2),
        ],  # History is inserted correctly for Node 1.
        [("system", "Node 2:"), ("human", f"Node 1: {user_input_2}")],
    ]
    assert [
        [(message.type, message.text()) for message in call] for call in llm.get_call_messages()
    ] == expected_call_messages


@django_db_with_data()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_llm_with_multiple_node_histories(get_llm_service, provider, pipeline, experiment_session, provider_model):
    llm = FakeLlmEcho()
    service = build_fake_llm_service(None, [0], llm)
    get_llm_service.return_value = service
    llm_1 = llm_response_with_prompt_node(
        str(provider.id),
        str(provider_model.id),
        prompt="Node 1:",
        history_type="node",
    )
    llm_2 = llm_response_with_prompt_node(
        str(provider.id),
        str(provider_model.id),
        prompt="Node 2:",
        history_type="node",
    )
    nodes = [start_node(), llm_1, llm_2, end_node()]
    runnable = create_runnable(pipeline, nodes)
    repo = ORMRepository()
    config = {"configurable": {"repo": repo}}

    user_input = "The User Input"
    output_1 = runnable.invoke(
        PipelineState(messages=[user_input], experiment_session=experiment_session), config=config
    )["messages"][-1]
    expected_output = f"Node 2: Node 1: {user_input}"
    assert output_1 == expected_output

    history = PipelineChatHistory.objects.get(session=experiment_session.id, name=llm_1["id"])
    assert history.type == "node"
    assert history.messages.count() == 1
    assert history.messages.first().as_tuples() == [("human", user_input), ("ai", f"Node 1: {user_input}")]

    history_2 = PipelineChatHistory.objects.get(session=experiment_session.id, name=llm_2["id"])
    assert history_2.type == "node"
    assert history_2.messages.count() == 1
    assert history_2.messages.first().as_tuples() == [
        ("human", f"Node 1: {user_input}"),
        ("ai", expected_output),
    ]

    user_input_2 = "Saying more stuff"
    output_2 = runnable.invoke(
        PipelineState(messages=[user_input_2], experiment_session=experiment_session), config=config
    )["messages"][-1]
    expected_output = f"Node 2: Node 1: {user_input_2}"
    assert output_2 == expected_output

    assert history.messages.count() == 2
    new_messages = history.messages.last().as_tuples()
    assert new_messages == [("human", user_input_2), ("ai", f"Node 1: {user_input_2}")]
    assert history_2.messages.count() == 2
    new_messages_2 = history_2.messages.last().as_tuples()
    assert new_messages_2 == [("human", f"Node 1: {user_input_2}"), ("ai", output_2)]

    expected_call_messages = [
        [("system", "Node 1:"), ("human", user_input)],
        [("system", "Node 2:"), ("human", f"Node 1: {user_input}")],
        [
            ("system", "Node 1:"),
            ("human", user_input),
            ("ai", f"Node 1: {user_input}"),
            ("human", user_input_2),
        ],  # History from node 1 is inserted
        [
            ("system", "Node 2:"),
            ("human", f"Node 1: {user_input}"),
            ("ai", f"Node 2: Node 1: {user_input}"),
            ("human", f"Node 1: {user_input_2}"),
        ],  # History from node 2 is inserted
    ]
    assert [
        [(message.type, message.text()) for message in call] for call in llm.get_call_messages()
    ] == expected_call_messages


@django_db_with_data()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_global_history(get_llm_service, provider, pipeline, experiment_session, provider_model):
    llm = FakeLlmEcho()
    service = build_fake_llm_service(None, [0], llm)
    get_llm_service.return_value = service

    llm_1 = llm_response_with_prompt_node(
        str(provider.id),
        str(provider_model.id),
        prompt="Node 1:",
        history_type="global",
    )
    llm_2 = llm_response_with_prompt_node(
        str(provider.id),
        str(provider_model.id),
        prompt="Node 2:",
        history_type="node",
    )
    nodes = [start_node(), llm_1, llm_2, end_node()]
    create_runnable(pipeline, nodes)
    pipeline.save()

    experiment = experiment_session.experiment
    experiment.pipeline_id = pipeline.id
    experiment.save()

    bot = PipelineBot(
        session=experiment_session,
        experiment=experiment,
        trace_service=TracingService.empty(),
    )

    def send_message(text):
        human_message = ChatMessage.objects.create(
            chat=experiment_session.chat, message_type=ChatMessageType.HUMAN, content=text
        )
        return bot.process_input(text, human_message=human_message)

    user_input = "The User Input"
    output_1 = send_message(user_input)
    user_input_2 = "Saying more stuff"
    output_2 = send_message(user_input_2)

    user_input_3 = "Tell me something interesting"
    send_message(user_input_3)

    expected_call_messages = [
        # First interaction with Node 1, no history yet
        [("system", "Node 1:"), ("human", user_input)],
        # First interaction with Node 2, no history yet
        [("system", "Node 2:"), ("human", f"Node 1: {user_input}")],
        # Second interaction with Node 1. The full output from the first run is inserted.
        [
            ("system", "Node 1:"),
            ("human", user_input),  # Input into Node 1 from the first run.
            ("ai", output_1.content),  # The output from the whole pipeline from the first run
            ("human", user_input_2),
        ],
        # Second interaction with Node 2. Only the history of Node 2 is inserted.
        [
            ("system", "Node 2:"),
            ("human", f"Node 1: {user_input}"),  # Input into Node 2 from the first run.
            ("ai", f"Node 2: Node 1: {user_input}"),  # Output of Node 2 from the first run.
            ("human", f"Node 1: {user_input_2}"),  # Input into Node 2 for this interaction
        ],
        # Third interaction with Node 1. The full output from the previous runs is inserted.
        [
            ("system", "Node 1:"),
            ("human", user_input),
            ("ai", output_1.content),  # The output from the whole pipeline from the first run
            ("human", user_input_2),
            ("ai", output_2.content),
            ("human", user_input_3),
        ],
        # Third interaction with Node 2. Only the history of Node 2 is inserted.
        [
            ("system", "Node 2:"),
            ("human", f"Node 1: {user_input}"),
            ("ai", f"Node 2: Node 1: {user_input}"),  # Output of Node 2 from the first run.
            ("human", f"Node 1: {user_input_2}"),
            ("ai", f"Node 2: Node 1: {user_input_2}"),  # Output of Node 2 from the second run.
            ("human", f"Node 1: {user_input_3}"),
        ],
    ]
    assert [
        [(message.type, message.text()) for message in call] for call in llm.get_call_messages()
    ] == expected_call_messages


@django_db_with_data()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_llm_with_named_history(get_llm_service, provider, pipeline, experiment_session, provider_model):
    llm = FakeLlmEcho()
    service = build_fake_llm_service(None, [0], llm)
    get_llm_service.return_value = service

    llm_1 = llm_response_with_prompt_node(
        str(provider.id),
        str(provider_model.id),
        prompt="Node 1:",
        history_type="named",
        history_name="history1",
        name="llm1",
    )
    llm_2 = llm_response_with_prompt_node(
        str(provider.id),
        str(provider_model.id),
        prompt="Node 2:",
        history_type="named",
        history_name="history1",
        name="llm2",
    )
    llm_3 = llm_response_with_prompt_node(str(provider.id), str(provider_model.id), prompt="Node 3:", history_type=None)
    nodes = [start_node(), llm_1, llm_2, llm_3, end_node()]
    runnable = create_runnable(pipeline, nodes)
    repo = ORMRepository()
    config = {"configurable": {"repo": repo}}

    user_input = "The User Input"
    runnable.invoke(PipelineState(messages=[user_input], experiment_session=experiment_session), config=config)
    user_input_2 = "Second User Input"
    runnable.invoke(PipelineState(messages=[user_input_2], experiment_session=experiment_session), config=config)

    expected_call_messages = [
        # First call to Node 1
        [("system", "Node 1:"), ("human", user_input)],
        # Call to Node 2. Since we are using the same history as for Node 1, it's history is included.
        [
            ("system", "Node 2:"),
            ("human", user_input),  # The input to Node 1
            ("ai", f"Node 1: {user_input}"),  # The output from node 1
            ("human", f"Node 1: {user_input}"),  # The input to Node 2
        ],
        # First call to Node 3, there is no history here.
        [("system", "Node 3:"), ("human", f"Node 2: Node 1: {user_input}")],
        # Second call to Node 1. Includes the full history from node 1 and node 2
        [
            ("system", "Node 1:"),
            ("human", user_input),  # First input
            ("ai", f"Node 1: {user_input}"),  # The output of node 1
            ("human", f"Node 1: {user_input}"),  # The input to Node 2
            ("ai", f"Node 2: Node 1: {user_input}"),  # The output of Node 2
            ("human", user_input_2),  # The second input to Node 1
        ],
        # Second call to Node 2. Includes the full history from node 1 and node 2
        [
            ("system", "Node 2:"),
            ("human", user_input),  # First input
            ("ai", f"Node 1: {user_input}"),  # The output of node 1
            ("human", f"Node 1: {user_input}"),  # The input to Node 2
            ("ai", f"Node 2: Node 1: {user_input}"),  # The output of Node 2
            ("human", user_input_2),  # The second input to Node 1
            ("ai", f"Node 1: {user_input_2}"),  # The second output of Node 1
            ("human", f"Node 1: {user_input_2}"),  # The second input to Node 2 (the output from Node 1)
        ],
        # Second Call to Node 3. Still no history is include, only the output of node 2 used as the input
        [
            ("system", "Node 3:"),
            ("human", f"Node 2: Node 1: {user_input_2}"),  # The output from node 2 into node 3
        ],
    ]
    assert [
        [(message.type, message.text()) for message in call] for call in llm.get_call_messages()
    ] == expected_call_messages


@django_db_with_data()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_llm_with_no_history(get_llm_service, provider, pipeline, experiment_session, provider_model):
    llm = FakeLlmEcho()
    service = build_fake_llm_service(None, [0], llm)
    get_llm_service.return_value = service

    llm_1 = llm_response_with_prompt_node(
        str(provider.id),
        str(provider_model.id),
        prompt="Node 1:",
        history_type="none",
    )
    nodes = [start_node(), llm_1, end_node()]
    runnable = create_runnable(pipeline, nodes)
    repo = ORMRepository()
    config = {"configurable": {"repo": repo}}

    user_input = "The User Input"
    runnable.invoke(PipelineState(messages=[user_input], experiment_session=experiment_session), config=config)
    user_input_2 = "Second User Input"
    runnable.invoke(PipelineState(messages=[user_input_2], experiment_session=experiment_session), config=config)

    expected_call_messages = [
        # First call to Node 1
        [("system", "Node 1:"), ("human", user_input)],
        # Second call to Node 1. Includes no history.
        [
            ("system", "Node 1:"),
            ("human", user_input_2),  # The second input to Node 1
        ],
    ]
    assert [
        [(message.type, message.text()) for message in call] for call in llm.get_call_messages()
    ] == expected_call_messages
