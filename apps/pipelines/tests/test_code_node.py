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


EXTRA_FUNCTION = """
def other(foo):
    return f"other {foo}"

return other(input)
"""

IMPORTS = """
import json
import datetime
import re
import time
return json.loads(input)
"""


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
@pytest.mark.parametrize(
    ("code", "input", "output"),
    [
        ("return f'Hello, {input}!'", "World", "Hello, World!"),
        ("", "foo", "foo"),  # No code just returns the input
        (EXTRA_FUNCTION, "blah", "other blah"),  # Calling a separate function is possible
        ("'foo'", "", "None"),  # No return value will return "None"
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


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_code_node_syntax_error(pipeline):
    nodes = [
        start_node(),
        code_node("this{}"),
        end_node(),
    ]
    with pytest.raises(PipelineNodeBuildError, match="SyntaxError: invalid syntax at statement: 'this{}'"):
        create_runnable(pipeline, nodes).invoke(PipelineState(messages=["World"]))["messages"][-1]


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
@pytest.mark.parametrize(
    ("code", "input", "error"),
    [
        ("import collections", "", "Importing 'collections' is not allowed"),
        ("return f'Hello, {blah}!'", "", "name 'blah' is not defined"),
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
