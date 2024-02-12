import io
import json

import pytest
from pandas import DataFrame, date_range

from apps.analysis.models import ResourceType
from apps.analysis.serializers import DataFramesSerializerV2, ResourceMetadata


@pytest.fixture()
def valid_dataframe():
    return DataFrame(index=date_range(start="1/1/2021", end="1/5/2021"), data={"value": range(5)})


@pytest.fixture()
def valid_metadata():
    return ResourceMetadata(
        type="dataframe.v1",
        format=ResourceType.JSON,
        data_schema={
            "index": "index",
            "fields": [{"name": "index", "type": "datetime"}, {"name": "value", "type": "int64"}],
        },
    )


def test_dataframes_serializer_writes_valid_dataframe(valid_dataframe):
    file = io.StringIO()
    DataFramesSerializerV2().write(valid_dataframe, file)
    assert json.loads(file.getvalue()) == [
        {"index": "2021-01-01T00:00:00.000", "value": 0},
        {"index": "2021-01-02T00:00:00.000", "value": 1},
        {"index": "2021-01-03T00:00:00.000", "value": 2},
        {"index": "2021-01-04T00:00:00.000", "value": 3},
        {"index": "2021-01-05T00:00:00.000", "value": 4},
    ]


def test_dataframes_serializer_round_trip(valid_dataframe, valid_metadata):
    file = io.StringIO()
    DataFramesSerializerV2().write(valid_dataframe, file)
    file.seek(0)
    df = DataFramesSerializerV2().read(file, valid_metadata)
    assert df.equals(valid_dataframe)


def test_dataframes_serializer_gets_correct_metadata(valid_dataframe):
    metadata = DataFramesSerializerV2().get_metadata(valid_dataframe)
    assert metadata.type == "dataframe.v2"
    assert metadata.format == ResourceType.JSON
    assert metadata.data_schema == {
        "fields": [{"name": "index", "type": "datetime"}, {"name": "value", "type": "integer"}],
        "index": "index",
    }
