import pytest

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
        ("{data.__class__}", ""),
        ("{data.__dict__}", ""),
        ("{data.__module__}", ""),
        ("{data.__code__}", ""),
        ("{data.__subclasses__()}", ""),
        ("{data.__init__}", ""),
        ("{data.__delattr__}", ""),
        ("{data.__getattribute__}", ""),
        ("{data.__setattr__}", ""),
        ("{data.__del__}", ""),
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
