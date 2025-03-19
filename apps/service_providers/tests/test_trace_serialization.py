from apps.channels.datamodels import Attachment
from apps.experiments.models import ExperimentSession
from apps.service_providers.tracing.service import serialize_input_output_dict


def test_serialize_trace_data():
    session = ExperimentSession()
    output = serialize_input_output_dict(
        {
            "key1": session,
            "key2": Attachment(file_id=123, type="file_search", name="file.txt", size=100),
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
        },
        "key3": [{"session": str(session)}],
    }
