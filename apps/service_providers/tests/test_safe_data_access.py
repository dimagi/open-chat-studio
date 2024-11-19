from typing import Any

import pydantic.v1
import pytest
from pydantic import BaseModel

from apps.service_providers.llm_service.prompt_context import SafeAccessWrapper


@pytest.mark.parametrize(
    ("prompt", "output"),
    [
        ("Hello, World!", "Hello, World!"),
        ("{data.name} ({data.age})", "John Doe (19)"),
        ("{data.name[1]}", "o"),
        ("{data.name.1}", "o"),
        ("{data.name.name}", ""),
        ("{data.age}", "19"),
        ("{data.age.0}", ""),
        ("{data.age[1]}", ""),
        ("{data[name]}!", "John Doe!"),
        ("{data['name']}", ""),  # should not be quoted
        ("{data.tasks[0].name}", "Task 1"),
        ("{data.tasks.0.name}", "Task 1"),
        ("{data.tasks.0[name]}", "Task 1"),
        ("{data.email}", ""),
        ("{data.tasks[0].due}", ""),
        ("{data.tasks[1]}", ""),
        ("{data.tasks.1}", ""),
        ("{data.tasks[1].name}", ""),
        ("{data.name[-1]}", "e"),
        ("{data.name.-1}", "e"),
        ("{data.tasks.-1.status}", "completed"),
        ("{data.name[1:2]}", ""),  # slice not supported
    ],
)
def test_format_participant_data(prompt, output):
    participant_data = {
        "name": "John Doe",
        "age": 19,
        "tasks": [
            {"name": "Task 1", "status": "completed"},
        ],
    }

    assert prompt.format(data=SafeAccessWrapper(participant_data)) == output


def test_pydantic():
    class PydanticModel(BaseModel):
        field: Any

    instance = PydanticModel(field=SafeAccessWrapper({"name": "John Doe"}))
    assert instance.model_dump() == {"field": {"__data": {"name": "John Doe"}}}
    assert instance.model_dump_json() == '{"field":{"__data":{"name":"John Doe"}}}'


def test_pydantic_v1():
    class PydanticModel(pydantic.v1.BaseModel):
        field: Any

    instance = PydanticModel(field=SafeAccessWrapper({"name": "John Doe"}))
    assert instance.dict() == {"field": {"__data": {"name": "John Doe"}}}
    assert instance.json() == '{"field": {"__data": {"name": "John Doe"}}}'
