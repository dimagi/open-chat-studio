import json
from unittest import mock

import pytest

from apps.pipelines.exceptions import PipelineNodeBuildError, PipelineNodeRunError
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.tests.utils import (
    code_node,
    create_runnable,
    end_node,
    start_node,
)
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.pytest import django_db_with_data


@pytest.fixture()
def pipeline():
    return PipelineFactory()


IMPORTS = """
import json
import datetime
import re
import time
def main(input):
    return json.loads(input)
"""


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
@pytest.mark.parametrize(
    ("code", "input", "output"),
    [
        ("def main(input):\n\treturn f'Hello, {input}!'", "World", "Hello, World!"),
        ("", "foo", "foo"),  # No code just returns the input
        ("def main(input):\n\t'foo'", "", "None"),  # No return value will return "None"
        (IMPORTS, json.dumps({"a": "b"}), str(json.loads('{"a": "b"}'))),  # Importing json will work
        ("def main(blah):\n\treturn f'Hello, {blah}!'", "World", "Hello, World!"),  # Renaming the argument works
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

def main(input):
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
        ("def main(input, others):\n\treturn input", "", "The main function should take a single argument as input"),
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
        ("import collections\ndef main(input):\n\treturn input", "", "Importing 'collections' is not allowed"),
        ("def main(input):\n\treturn f'Hello, {blah}!'", "", "name 'blah' is not defined"),
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
