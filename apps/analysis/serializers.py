import json
import tempfile
from typing import IO, Any

import pandas as pd
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from apps.analysis.models import Resource, ResourceMetadata


def create_resource_for_data(team, data: Any, name: str) -> Resource:
    serializer = get_serializer_by_type(data)
    metadata = serializer.get_metadata(data)
    resource = Resource(
        team=team,
        name=name,
        type=metadata.format,
        metadata=metadata.model_dump(),
    )
    with tempfile.TemporaryFile(mode="w+") as file:
        serializer.write(data, file)
        file.seek(0)
        resource.file.save(f"{resource.name}.{metadata.format}", file)
    resource.save()
    return resource


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
        return ResourceMetadata(type=type(data).__name__, format="json", data_schema={})

    def get_summary(self, data: Any) -> str:
        if type(data) == str:
            return data
        return json.dumps(data, indent=2, cls=DjangoJSONEncoder)


class DataFramesSerializer(Serializer):
    supported_types = [pd.DataFrame]
    supported_type_names = ["dataframe"]

    def read(self, file: IO, metadata: ResourceMetadata) -> Any:
        date_cols = [field["name"] for field in metadata.data_schema["fields"] if field["type"] == "datetime"]
        data = pd.read_json(file, orient="records", lines=True, convert_dates=date_cols)
        if "index" in metadata.data_schema:
            data = data.set_index(metadata.data_schema["index"])
        return data

    def write(self, data: pd.DataFrame, file: IO):
        if not isinstance(data.index, pd.RangeIndex):
            data = data.reset_index()
        data.to_json(file, orient="records", lines=True, date_format="iso")

    def get_metadata(self, data: Any) -> ResourceMetadata:
        schema = pd.io.json.build_table_schema(data, version=False)
        if not isinstance(data.index, pd.RangeIndex):
            schema["index"] = data.index.name or "index"
        if "primaryKey" in schema:
            del schema["primaryKey"]
        return ResourceMetadata(
            type="dataframe",
            format="jsonl",
            data_schema=schema,
        )

    def get_summary(self, data: Any) -> str:
        return str(data)


def get_serializer_by_type(data: Any) -> Serializer:
    for serializer in [BasicTypeSerializer, DataFramesSerializer]:
        if type(data) in serializer.supported_types:
            return serializer()
    raise NotImplementedError(f"No serializer found for {type(data)}")


def get_serializer_by_name(type_name: str) -> Serializer:
    for serializer in [BasicTypeSerializer, DataFramesSerializer]:
        if type_name in serializer.supported_type_names:
            return serializer()
    raise NotImplementedError(f"No serializer found for {type_name}")
