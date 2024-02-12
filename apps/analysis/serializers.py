import contextlib
import json
import tempfile
from typing import IO, Any

import pandas as pd
from django.core.files.base import ContentFile
from django.core.serializers.json import DjangoJSONEncoder

from apps.analysis.models import Resource, ResourceMetadata, ResourceType


def create_resource_for_data(team, data: Any, name: str) -> Resource:
    serializer = get_serializer_by_type(data)
    metadata = serializer.get_metadata(data)
    resource = Resource(
        team=team,
        name=name,
        type=metadata.format,
        metadata=metadata.model_dump(exclude={"content_type"}),
        content_type=metadata.content_type,
    )
    with temporary_data_file(data) as file:
        resource.file.save(f"{resource.name}.{metadata.format}", file)
    resource.save()
    return resource


def create_resource_for_raw_data(team, data: Any, name: str, metadata: ResourceMetadata) -> Resource:
    resource = Resource(
        team=team,
        name=name,
        type=metadata.format,
        metadata=metadata.model_dump(exclude={"content_type"}),
        content_type=metadata.content_type,
    )
    ext = ""
    if "." not in name and metadata.format not in (ResourceType.UNKNOWN, ResourceType.IMAGE):
        ext = f".{metadata.format}"
    resource.file.save(f"{resource.name}{ext}", ContentFile(data))
    resource.save()
    return resource


@contextlib.contextmanager
def temporary_data_file(data: Any) -> IO:
    serializer = get_serializer_by_type(data)
    with tempfile.NamedTemporaryFile(mode="w+", suffix=f".{serializer.get_metadata(data).format}") as file:
        serializer.write(data, file)
        file.seek(0)
        yield file


class Serializer:
    """Base class for serializing data output from a pipeline step"""

    supported_types: list[type] = None
    supported_type_names: list[str] = None
    """Data types supported by this serializer"""

    def read(self, file: IO, metadata: ResourceMetadata) -> Any:
        """Given a file and metadata, read the data from the file and return it."""
        raise NotImplementedError()

    def write(self, data: Any, file: IO):
        """Given data and a file, write the data to the file."""
        raise NotImplementedError()

    def get_metadata(self, data: Any) -> ResourceMetadata:
        """Given data, return the metadata for the resource."""
        raise NotImplementedError()

    def get_summary(self, data: Any) -> str:
        """Given data, return a summary of the data as a string"""
        raise NotImplementedError()


class BasicTypeSerializer(Serializer):
    supported_types = [str, dict, list]
    supported_type_names = [d.__name__ for d in supported_types]

    def read(self, file: IO, metadata: ResourceMetadata) -> Any:
        return json.loads(file.read())["data"]

    def write(self, data: Any, file: IO):
        json.dump({"data": data}, file, cls=DjangoJSONEncoder)

    def get_metadata(self, data: Any) -> ResourceMetadata:
        return ResourceMetadata(
            type=type(data).__name__, format="json", data_schema={}, content_type="application/json"
        )

    def get_summary(self, data: Any) -> str:
        if isinstance(data, str):
            return data
        return json.dumps(data, indent=2, cls=DjangoJSONEncoder)


class DataFramesSerializer(Serializer):
    supported_types = []
    supported_type_names = ["dataframe"]

    def read(self, file: IO, metadata: ResourceMetadata) -> Any:
        date_cols = [field["name"] for field in metadata.data_schema["fields"] if field["type"] == "datetime"]
        data = pd.read_json(file, orient="records", lines=True, convert_dates=date_cols)
        if "index" in metadata.data_schema:
            data = data.set_index(metadata.data_schema["index"])
        return data

    def get_summary(self, data: Any) -> str:
        return str(data)


class DataFramesSerializerV1(Serializer):
    supported_types = [pd.DataFrame]
    supported_type_names = ["dataframe.v1"]

    def read(self, file: IO, metadata: ResourceMetadata) -> Any:
        return pd.read_json(file, orient="table")

    def get_summary(self, data: Any) -> str:
        return str(data)


class DataFramesSerializerV2(Serializer):
    supported_types = [pd.DataFrame]
    supported_type_names = ["dataframe.v2"]

    def read(self, file: IO, metadata: ResourceMetadata) -> Any:
        date_cols = [field["name"] for field in metadata.data_schema["fields"] if field["type"] == "datetime"]
        data = pd.read_json(file, orient="records", convert_dates=date_cols)
        if "index" in metadata.data_schema:
            data = data.set_index(metadata.data_schema["index"])
        return data

    def write(self, data: pd.DataFrame, file: IO):
        if not isinstance(data.index, pd.RangeIndex):
            data = data.reset_index()
        data.to_json(file, orient="records", date_format="iso")

    def get_metadata(self, data: Any) -> ResourceMetadata:
        schema = pd.io.json.build_table_schema(data, version=False)
        if not isinstance(data.index, pd.RangeIndex):
            schema["index"] = data.index.name or "index"
        if "primaryKey" in schema:
            del schema["primaryKey"]
        return ResourceMetadata(
            type="dataframe.v2",
            format=ResourceType.JSON,
            data_schema=schema,
            content_type="application/json",
        )

    def get_summary(self, data: Any) -> str:
        return str(data)


def get_serializer_by_type(data: Any) -> Serializer:
    for serializer in [BasicTypeSerializer, DataFramesSerializerV2]:
        if type(data) in serializer.supported_types:
            return serializer()
    raise NotImplementedError(f"No serializer found for {type(data)}")


def get_serializer_by_name(type_name: str) -> Serializer:
    for serializer in [BasicTypeSerializer, DataFramesSerializer, DataFramesSerializerV2]:
        if type_name in serializer.supported_type_names:
            return serializer()
    raise NotImplementedError(f"No serializer found for {type_name}")
