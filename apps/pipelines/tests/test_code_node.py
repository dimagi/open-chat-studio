import json
from unittest import mock

import pytest

from apps.channels.datamodels import Attachment
from apps.experiments.models import ParticipantData
from apps.files.models import File
from apps.pipelines.exceptions import PipelineNodeBuildError, PipelineNodeRunError
from apps.pipelines.nodes.base import PipelineState
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


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
@pytest.mark.parametrize(
    ("code", "input", "output"),
    [
        ("def main(input, **kwargs):\n\treturn f'Hello, {input}!'", "World", "Hello, World!"),
        ("", "foo", "foo"),  # No code just returns the input
        ("def main(input, **kwargs):\n\t'foo'", "", "None"),  # No return value will return "None"
        (IMPORTS, json.dumps({"a": "b"}), str(json.loads('{"a": "b"}'))),  # Importing json will work
    ],
)
def test_code_node(pipeline, code, input, output):
    nodes = [
        start_node(),
        code_node(code),
        end_node(),
    ]
    assert create_runnable(pipeline, nodes).invoke(PipelineState(messages=[input]))["messages"][-1] == output


EXTRA_FUNCTION = """
def other(foo):
    return f"other {foo}"

def main(input, **kwargs):
    return other(input)
"""


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
@pytest.mark.parametrize(
    ("code", "input", "error"),
    [
        ("this{}", "", "SyntaxError: invalid syntax at statement: 'this{}"),
        (
            EXTRA_FUNCTION,
            "",
            (
                "You can only define a single function, 'main' at the top level. "
                "You may use nested functions inside that function if required"
            ),
        ),
        ("def other(input):\n\treturn input", "", "You must define a 'main' function"),
        (
            "def main(input, others, **kwargs):\n\treturn input",
            "",
            r"The main function should have the signature main\(input, \*\*kwargs\) only\.",
        ),
        (
            """def main(intput, **kwargs):\n\tget_temp_state_key("attachments")[0]._file.delete()\n\treturn input""",
            "",
            """"_file" is an invalid attribute name because it starts with "_".""",
        ),
        ("import PyPDF2\ndef main(input):\n\treturn input", "", "No module named 'PyPDF2'"),
    ],
)
def test_code_node_build_errors(pipeline, code, input, error):
    nodes = [
        start_node(),
        code_node(code),
        end_node(),
    ]
    with pytest.raises(PipelineNodeBuildError, match=error):
        create_runnable(pipeline, nodes).invoke(PipelineState(messages=[input]))["messages"][-1]


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
@pytest.mark.parametrize(
    ("code", "input", "error"),
    [
        (
            "import collections\ndef main(input, **kwargs):\n\treturn input",
            "",
            "Importing 'collections' is not allowed",
        ),
        ("def main(input, **kwargs):\n\treturn f'Hello, {blah}!'", "", "name 'blah' is not defined"),
    ],
)
def test_code_node_runtime_errors(pipeline, code, input, error):
    nodes = [
        start_node(),
        code_node(code),
        end_node(),
    ]
    with pytest.raises(PipelineNodeRunError, match=error):
        create_runnable(pipeline, nodes).invoke(PipelineState(messages=[input]))["messages"][-1]


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_get_participant_data(pipeline, experiment_session):
    ParticipantData.objects.create(
        team=experiment_session.team,
        experiment=experiment_session.experiment,
        participant=experiment_session.participant,
        data={"fun_facts": {"personality": "fun loving", "body_type": "robot"}},
    )

    code = """
def main(input, **kwargs):
    return get_participant_data()["fun_facts"]["body_type"]
"""
    nodes = [
        start_node(),
        code_node(code),
        end_node(),
    ]
    assert (
        create_runnable(pipeline, nodes).invoke(PipelineState(experiment_session=experiment_session, messages=["hi"]))[
            "messages"
        ][-1]
        == "robot"
    )


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_update_participant_data(pipeline, experiment_session):
    output = "moody"
    participant_data = ParticipantData.objects.create(
        team=experiment_session.team,
        experiment=experiment_session.experiment,
        participant=experiment_session.participant,
        data={"fun_facts": {"personality": "fun loving", "body_type": "robot"}},
    )

    code = f"""
def main(input, **kwargs):
    data = get_participant_data()
    data["fun_facts"]["personality"] = "{output}"
    set_participant_data(data)
    return get_participant_data()["fun_facts"]["personality"]
"""
    nodes = [
        start_node(),
        code_node(code),
        end_node(),
    ]
    assert (
        create_runnable(pipeline, nodes).invoke(PipelineState(experiment_session=experiment_session, messages=["hi"]))[
            "messages"
        ][-1]
        == output
    )
    participant_data.refresh_from_db()
    assert participant_data.data["fun_facts"]["personality"] == output


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_temp_state(pipeline, experiment_session):
    output = "['fun loving', 'likes puppies']"
    code_set = f"""
def main(input, **kwargs):
    return set_temp_state_key("fun_facts", {output})
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
    assert (
        create_runnable(pipeline, nodes).invoke(PipelineState(experiment_session=experiment_session, messages=["hi"]))[
            "messages"
        ][-1]
        == output
    )


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_temp_state_get_outputs(pipeline, experiment_session):
    # Temp state contains the outputs of the previous nodes

    input = "hello"
    code_get = """
def main(input, **kwargs):
    return str(get_temp_state_key("outputs"))
"""
    nodes = [
        start_node(),
        passthrough_node(),
        render_template_node("<b>The input is: {{ input }}</b>"),
        code_node(code_get),
        end_node(),
    ]
    assert create_runnable(pipeline, nodes).invoke(
        PipelineState(experiment_session=experiment_session, messages=[input])
    )["messages"][-1] == str(
        {
            "start": input,
            "passthrough": input,
            "render template": f"<b>The input is: {input}</b>",
        }
    )


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_temp_state_set_outputs(pipeline, experiment_session):
    input = "hello"
    code_set = """
def main(input, **kwargs):
    set_temp_state_key("outputs", "foobar")
    return input
"""
    nodes = [
        start_node(),
        code_node(code_set),
        end_node(),
    ]
    with pytest.raises(PipelineNodeRunError, match="Cannot set the 'outputs' key of the temporary state"):
        create_runnable(pipeline, nodes).invoke(PipelineState(experiment_session=experiment_session, messages=[input]))[
            "messages"
        ][-1]


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_temp_state_user_input(pipeline, experiment_session):
    # Temp state contains the user input

    input = "hello"
    code_get = """
def main(input, **kwargs):
    return str(get_temp_state_key("user_input"))
"""
    nodes = [
        start_node(),
        code_node(code_get),
        end_node(),
    ]
    assert (
        create_runnable(pipeline, nodes).invoke(PipelineState(experiment_session=experiment_session, messages=[input]))[
            "messages"
        ][-1]
        == input
    )


@django_db_with_data(available_apps=("apps.service_providers",))
def test_read_attachments(pipeline, experiment_session):
    file = File.from_content("foo.txt", b"from file", "text/plain", experiment_session.team.id)

    code_get = """
def main(input, **kwargs):
    return f'content {get_temp_state_key("attachments")[0].read_text()}'
"""
    nodes = [
        start_node(),
        code_node(code_get),
        end_node(),
    ]
    state = PipelineState(
        experiment_session=experiment_session,
        messages=["hi"],
        attachments=[Attachment.from_file(file, "code_interpreter")],
    )
    assert create_runnable(pipeline, nodes).invoke(state)["messages"][-1] == "content from file"
    File.objects.get(id=file.id)
