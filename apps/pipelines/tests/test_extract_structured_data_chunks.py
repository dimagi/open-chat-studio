import json
from unittest.mock import MagicMock

from apps.pipelines.nodes.mixins import ExtractStructuredDataNodeMixin


class _Output:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


class _StubNode(ExtractStructuredDataNodeMixin):
    """Runs `_process` over fixed chunks, with each chunk's extraction result supplied by the test."""

    name = "extract"
    data_schema = json.dumps({})
    _config = None

    def __init__(self, reference_data, chunk_outputs):
        self.reference_data = reference_data
        self.chunk_outputs = list(chunk_outputs)
        self.seen_reference_data = []

    def get_tool_class(self, data):
        return MagicMock()

    def get_reference_data(self, context):
        return self.reference_data

    def _get_prompt_token_count(self, reference_data, json_schema):
        return 0

    def chunk_messages(self, input, prompt_token_count):
        return [f"chunk {i}" for i in range(len(self.chunk_outputs))]

    def extraction_chain(self, tool_class, reference_data):
        self.seen_reference_data.append(reference_data)
        chain = MagicMock()
        chain.invoke.return_value = _Output(self.chunk_outputs[len(self.seen_reference_data) - 1])
        return chain

    def update_reference_data(self, new_data, reference_data):
        if isinstance(reference_data, dict):
            return reference_data | new_data
        return new_data

    def get_node_output(self, context, output_data):
        return output_data


def test_later_chunks_add_to_data_from_earlier_chunks():
    node = _StubNode({"city": "Cape Town"}, [{"name": "Ann"}, {"age": 30}])

    result = node._process(MagicMock(), MagicMock())

    assert result == {"city": "Cape Town", "name": "Ann", "age": 30}
    assert node.seen_reference_data[1] == {"city": "Cape Town", "name": "Ann"}
