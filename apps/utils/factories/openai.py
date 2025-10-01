import factory
from openai.types import FileObject
from openai.types.beta import Assistant


class AssistantFactory(factory.Factory):
    class Meta:
        model = Assistant

    id = factory.Faker("uuid4")
    created_at = factory.Faker("pyint")
    name = factory.Sequence(lambda n: f"Test Assistant {n}")
    description = factory.Faker("sentence")
    temperature = 0.9
    top_p = 1.0
    file_ids = []
    metadata = None
    object = "assistant"
    instructions = factory.Faker("sentence")
    model = factory.Faker("random_element", elements=["gpt4", "gpt-3.5-turbo"])
    tools = [
        {"type": "code_interpreter"},
        {"type": "file_search"},
        {"type": "function", "function": {"name": "test", "parameters": {"test": {"type": "string"}}}},
    ]
    tool_resources = {
        "code_interpreter": {"file_ids": ["file_123"]},
        "file_search": {"vector_store_ids": ["vs_123"]},
    }


class FileObjectFactory(factory.Factory):
    class Meta:
        model = FileObject

    id = factory.Faker("uuid4")
    bytes = factory.Faker("pyint")
    created_at = factory.Faker("pyint")
    filename = factory.Faker("file_name")
    object = "file"
    purpose = "assistants"
    status = "processed"
