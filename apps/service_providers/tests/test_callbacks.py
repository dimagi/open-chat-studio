from apps.channels.datamodels import Attachment
from apps.experiments.models import ExperimentSession
from apps.service_providers.tracing.callback import (
    LANGSMITH_TAG_HIDDEN,
    NameMappingWrapper,
    serialize_input_output_dict,
)


def test_serialize_trace_data():
    session = ExperimentSession()
    output = serialize_input_output_dict(
        {
            "key1": session,
            "key2": Attachment(
                file_id=123, type="file_search", name="file.txt", size=100, download_link="https://localhost:8000"
            ),
            "key3": [{"session": session}],
        }
    )
    assert output == {
        "key1": str(session),
        "key2": {
            "content_type": "application/octet-stream",
            "file_id": 123,
            "name": "file.txt",
            "size": 100,
            "type": "file_search",
            "upload_to_assistant": False,
            "download_link": "https://localhost:8000",
        },
        "key3": [{"session": str(session)}],
    }


def test_filter_patterns():
    class MockCallback:
        def on_llm_start(self, serialized, prompts, tags=None, **kwargs):
            assert tags is not None
            assert LANGSMITH_TAG_HIDDEN in tags
            assert kwargs["name"] == "llm_name"

    callback = MockCallback()
    filter_patterns = ["llm_name"]
    wrapper = NameMappingWrapper(callback, {}, filter_patterns)
    wrapper.on_llm_start({"name": "llm_name"}, ["prompt"])


def test_name_map():
    class MockCallback:
        def on_llm_start(self, serialized, prompts, tags=None, **kwargs):
            assert tags is not None
            assert LANGSMITH_TAG_HIDDEN not in tags
            assert kwargs["name"] == "mapped_name"

    callback = MockCallback()
    wrapper = NameMappingWrapper(callback, {"llm_name": "mapped_name"}, [])
    wrapper.on_llm_start({"name": "llm_name"}, ["prompt"])
