import json

import pytest
from django.core.files.base import ContentFile
from pydantic import ValidationError

from apps.channels.datamodels import Attachment
from apps.files.models import File
from apps.pipelines.exceptions import CodeNodeRunError
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.nodes import CodeNode
from apps.pipelines.tests.utils import (
    code_node,
    create_runnable,
    end_node,
    passthrough_node,
    render_template_node,
    start_node,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.pytest import django_db_with_data


@pytest.fixture()
def pipeline():
    return PipelineFactory()


@pytest.fixture()
def experiment_session():
    return ExperimentSessionFactory()


IMPORTS = """
import json
import datetime
import re
import time
def main(input, **kwargs):
    return json.loads(input)
"""


# @django_db_with_data()
@pytest.mark.parametrize(
    ("code", "user_input", "output"),
    [
        ("def main(input, **kwargs):\n\treturn f'Hello, {input}!'", "World", "Hello, World!"),
        ("", "foo", "foo"),  # No code just returns the input
        ("def main(input, **kwargs):\n\t'foo'", "", "None"),  # No return value will return "None"
        ("def main(input, **kwargs):\n\treturn kwargs['node_inputs']", "hi", "['hi']"),  # access node inputs
        (IMPORTS, json.dumps({"a": "b"}), str(json.loads('{"a": "b"}'))),  # Importing json will work
    ],
)
def test_code_node(code, user_input, output):
    node = CodeNode(name="test", node_id="123", django_node=None, code=code)
    node_output = node._process(
        PipelineState(outputs={}, experiment_session=None, last_node_input=user_input, node_inputs=[user_input])
    )
    assert node_output.update["messages"][-1] == output


EXTRA_FUNCTION = """
def other(foo):
    return f"other {foo}"

def main(input, **kwargs):
    return other(input)
"""


@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("this{}", "SyntaxError: invalid syntax at statement: 'this{}"),
        (
            EXTRA_FUNCTION,
            (
                "You can only define a single function, 'main' at the top level. "
                "You may use nested functions inside that function if required"
            ),
        ),
        ("def other(input):\n\treturn input", "You must define a 'main' function"),
        (
            "def main(input, others, **kwargs):\n\treturn input",
            r"The main function should have the signature main\(input, \*\*kwargs\) only\.",
        ),
        (
            """def main(intput, **kwargs):\n\tget_temp_state_key("attachments")[0]._file.delete()\n\treturn input""",
            """"_file" is an invalid attribute name because it starts with "_".""",
        ),
        ("import PyPDF2\ndef main(input):\n\treturn input", "No module named 'PyPDF2'"),
    ],
)
def test_code_node_build_errors(code, error):
    with pytest.raises(ValidationError, match=error):
        CodeNode(name="test", node_id="123", django_node=None, code=code)


@pytest.mark.parametrize(
    ("code", "user_input", "error"),
    [
        (
            "import collections\ndef main(input, **kwargs):\n\treturn input",
            "",
            "Importing 'collections' is not allowed",
        ),
        ("def main(input, **kwargs):\n\treturn f'Hello, {blah}!'", "", "name 'blah' is not defined"),
    ],
)
def test_code_node_runtime_errors(code, user_input, error):
    node = CodeNode(name="test", node_id="123", django_node=None, code=code)
    with pytest.raises(CodeNodeRunError, match=error):
        node._process(
            PipelineState(outputs={}, experiment_session=None, last_node_input=user_input, node_inputs=[user_input])
        )


@pytest.mark.django_db()
def test_get_participant_data(pipeline, experiment_session):
    code = """
def main(input, **kwargs):
    return get_participant_data()["fun_facts"]["body_type"]
"""
    node = CodeNode(name="test", node_id="123", django_node=None, code=code)
    node_output = node._process(
        PipelineState(
            last_node_input="hi",
            node_inputs=["hi"],
            outputs={},
            experiment_session=experiment_session,
            participant_data={"fun_facts": {"personality": "fun loving", "body_type": "robot"}},
        ),
    )
    assert node_output.update["messages"][-1] == "robot"


@pytest.mark.django_db()
def test_update_participant_data(pipeline, experiment_session):
    output = "moody"

    code = f"""
def main(input, **kwargs):
    data = get_participant_data()
    data["fun_facts"]["personality"] = "{output}"
    set_participant_data(data)
    return get_participant_data()["fun_facts"]["personality"]
"""
    node = CodeNode(name="test", node_id="123", django_node=None, code=code)
    node_output = node._process(
        PipelineState(
            last_node_input="hi",
            node_inputs=["hi"],
            outputs={},
            experiment_session=experiment_session,
            participant_data={"fun_facts": {"personality": "fun loving", "body_type": "robot"}},
        ),
    )
    assert node_output.update["messages"][-1] == output
    assert node_output.update["participant_data"]["fun_facts"]["personality"] == output


@django_db_with_data()
def test_participant_data_across_multiple_nodes(pipeline, experiment_session):
    code_set = """
def main(input, **kwargs):
    set_participant_data_key("test", "value")
    return input
"""
    code_get = """
def main(input, **kwargs):
    return str(get_participant_data()["test"])
"""
    nodes = [
        start_node(),
        code_node(code_set),
        code_node(code_get),
        end_node(),
    ]
    node_output = create_runnable(pipeline, nodes).invoke(
        PipelineState(experiment_session=experiment_session, messages=["hi"])
    )
    assert node_output["messages"][-1] == "value"


@django_db_with_data()
def test_temp_state_across_multiple_nodes(pipeline, experiment_session):
    output = "['fun loving', 'likes puppies']"
    code_set = f"""
def main(input, **kwargs):
    set_temp_state_key("fun_facts", {output})
    return input
"""
    code_get = """
def main(input, **kwargs):
    return str(get_temp_state_key("fun_facts"))
"""
    nodes = [
        start_node(),
        code_node(code_set),
        code_node(code_get),
        end_node(),
    ]
    node_output = create_runnable(pipeline, nodes).invoke(
        PipelineState(experiment_session=experiment_session, messages=["hi"])
    )
    assert node_output["messages"][-1] == output


@django_db_with_data()
def test_temp_state_get_outputs(pipeline, experiment_session):
    # Temp state contains the outputs of the previous nodes

    input = "hello"
    code_get = """
def main(input, **kwargs):
    return str(get_temp_state_key("outputs"))
"""
    template_node = render_template_node("<b>The input is: {{ input }}</b>", name="template")
    nodes = [
        start_node(),
        passthrough_node(name="passthrough"),
        template_node,
        code_node(code_get),
        end_node(),
    ]
    assert create_runnable(pipeline, nodes).invoke(
        PipelineState(experiment_session=experiment_session, messages=[input])
    )["messages"][-1] == str({
        "start": input,
        "passthrough": input,
        "template": f"<b>The input is: {input}</b>",
    })


def test_temp_state_set_outputs():
    code_set = """
def main(input, **kwargs):
    set_temp_state_key("outputs", "foobar")
    return input
"""
    node = CodeNode(name="test", node_id="123", django_node=None, code=code_set)
    with pytest.raises(CodeNodeRunError, match="Cannot set the 'outputs' key of the temporary state"):
        node._process(PipelineState(outputs={}, experiment_session=None, last_node_input="hi", node_inputs=["hi"]))


def test_temp_state_user_input():
    # Temp state contains the user input

    user_input = "hello"
    code_get = """
def main(input, **kwargs):
    return str(get_temp_state_key("user_input"))
"""
    node = CodeNode(name="test", node_id="123", django_node=None, code=code_get)
    state = PipelineState(messages=[user_input], outputs={}, experiment_session=None, temp_state={})
    node_output = node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config={})
    assert node_output.update["messages"][-1] == user_input


@pytest.mark.django_db()
def test_read_attachments(pipeline, experiment_session):
    file_obj = ContentFile("from file")
    file = File.create("foo.txt", file_obj, experiment_session.team.id)

    code_get = """
def main(input, **kwargs):
    return f'content {get_temp_state_key("attachments")[0].read_text()}'
"""
    node = CodeNode(name="test", node_id="123", django_node=None, code=code_get)
    state = PipelineState(
        outputs={},
        experiment_session=experiment_session,
        messages=["hi"],
        attachments=[Attachment.from_file(file, "code_interpreter", experiment_session.id)],
        temp_state={},
    )
    node_output = node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config={})
    assert node_output.update["messages"][-1] == "content from file"


@pytest.mark.django_db()
def test_get_participant_schedules(pipeline, experiment_session):
    """
    Test that the get_participant_schedules function correctly retrieves
    scheduled messages for the experiment session's participant and experiment.
    """
    from django.utils import timezone

    from apps.events.models import EventActionType, TimePeriod
    from apps.utils.factories.events import EventActionFactory, ScheduledMessageFactory

    params = {
        "name": "Test",
        "time_period": TimePeriod.DAYS,
        "frequency": 1,
        "repetitions": 1,
        "prompt_text": "",
        "experiment_id": experiment_session.experiment.id,
    }
    event_action = EventActionFactory(params=params, action_type=EventActionType.SCHEDULETRIGGER)

    ScheduledMessageFactory(
        experiment=experiment_session.experiment,
        team=experiment_session.team,
        participant=experiment_session.participant,
        action=event_action,
        next_trigger_date=timezone.now(),
        is_complete=False,
        cancelled_at=None,
    )
    code = """
def main(input, **kwargs):
    schedules = get_participant_schedules()
    return f"Number of schedules: {len(schedules)}"
"""
    node = CodeNode(name="test", node_id="123", django_node=None, code=code)
    state = PipelineState(messages=["hi"], outputs={}, experiment_session=experiment_session, temp_state={})
    node_output = node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config={})
    assert node_output.update["messages"][-1] == "Number of schedules: 1"


@pytest.mark.django_db()
def test_get_participant_schedules_empty(experiment_session):
    """
    Test that the get_participant_schedules function returns an empty list
    when there are no active scheduled messages.
    """
    code = """
def main(input, **kwargs):
    schedules = get_participant_schedules()
    return f"Number of schedules: {len(schedules)}, Empty list: {schedules == []}"
"""
    node = CodeNode(name="test", node_id="123", django_node=None, code=code)
    state = PipelineState(messages=["hi"], outputs={}, experiment_session=experiment_session, temp_state={})
    node_output = node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config={})
    assert node_output.update["messages"][-1] == "Number of schedules: 0, Empty list: True"


@pytest.mark.django_db()
def test_get_and_set_session_state(experiment_session):
    code = """
def main(input, **kwargs):
    msg_count = get_session_state_key("message_count") or 1
    set_session_state_key("message_count", msg_count + 1)
    return input
    """
    node = CodeNode(name="test", node_id="123", django_node=None, code=code)
    state = PipelineState(
        messages=["hi"], outputs={}, experiment_session=experiment_session, temp_state={}, session_state={}
    )
    output = node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config={})
    assert output.update["session_state"] == {"message_count": 2}


def test_tags_mocked():
    code_set = """
def main(input, **kwargs):
    add_session_tag("session-tag")
    add_message_tag("message-tag")
    return input
    """
    node = CodeNode(name="test", node_id="123", django_node=None, code=code_set)
    output = node._process(PipelineState(outputs={}, experiment_session=None, last_node_input="hi", node_inputs=["hi"]))
    assert output.update["output_message_tags"] == [("message-tag", "")]
    assert output.update["session_tags"] == [("session-tag", "")]


def test_set_list():
    code_set = """
def main(input, **kwargs):
    quiz = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
    questions_asked = [1, "2", 2]
    unasked_questions = quiz
    if questions_asked:
        normalized_questions_asked = set()
        for question_id in questions_asked:
            try:
                normalized_questions_asked.add(int(question_id))
            except:
                pass
        unasked_questions = [question["id"] for question in quiz if question["id"] not in normalized_questions_asked]

    unasked_ids = ",".join([str(q) for q in unasked_questions])
    return f"{unasked_ids} - {normalized_questions_asked}"
    """

    node = CodeNode(name="test", node_id="123", django_node=None, code=code_set)
    node_output = node._process(
        PipelineState(outputs={}, experiment_session=None, last_node_input="hi", node_inputs=["hi"])
    )
    assert node_output.update["messages"][-1] == "3,4 - {1, 2}"


def test_traceback():
    code_set = """
def main(input, **kwargs):
    # this is a comment
    a = 1
    b = 2
    if a != b:
       fail("asfd")
    return input
    """

    node = CodeNode(name="test", node_id="123", django_node=None, code=code_set)
    with pytest.raises(CodeNodeRunError) as exc_info:
        node._process(PipelineState(outputs={}, experiment_session=None, last_node_input="hi", node_inputs=["hi"]))
    assert (
        str(exc_info.value)
        == """Error: NameError("name 'fail' is not defined")
Context:
      5:     b = 2
      6:     if a != b:
>>>   7:        fail("asfd")
      8:     return input
      9:     """
    )


@pytest.mark.parametrize(
    ("key_name", "should_raise"),
    [
        # Valid cases - should not raise
        # ("custom_key", False),
        # ("other_key", False),
        # # Reserved key - should raise
        # ("remote_context", True),
        # ("attachments", True),
        # (" remote_context ", True),
        (" remote_context", True),
        # ("remote_context ", True),
    ],
)
def test_code_node_reserved_session_state_keys(key_name, should_raise):
    """Test that CodeNode validates and prevents setting reserved session state keys"""
    code = f"""def main(input, **kwargs):
    set_session_state_key("{key_name}", "value")
    return input"""

    if should_raise:
        with pytest.raises(ValidationError, match="reserved_key_used"):
            CodeNode(name="test", node_id="123", django_node=None, code=code)
    else:
        node = CodeNode(name="test", node_id="123", django_node=None, code=code)
        assert node is not None
