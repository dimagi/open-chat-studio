import json
import tempfile
from typing import IO, Any

import pandas as pd
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from apps.analysis.models import Resource, ResourceMetadata


def create_resource_for_data(team, data: Any, name: str) -> Resource:
    serializer = get_serializer(data)
    metadata = serializer.get_metadata(data)
    resource = Resource(
        team=team,
        name=f"{name} {timezone.now().isoformat()}",
        type=metadata.type,
        metadata=metadata.model_dump(),
    )
    with tempfile.TemporaryFile(mode="w+") as file:
        serializer.write(data, file)
        file.seek(0)
        resource.file.save(f"{resource.name}.{metadata.format}", file)
    resource.save()
    return resource


class Serializer:
    supported_types: list[type] = None

    def read(self, file: IO, metadata: ResourceMetadata) -> Any:
        raise NotImplementedError()

    def write(self, data: Any, file: IO):
        raise NotImplementedError()

    def get_metadata(self, data: Any) -> ResourceMetadata:
        raise NotImplementedError()


class BasicTypeSerializer(Serializer):
    supported_types = [str, dict, list]

    def read(self, file: IO, metadata: ResourceMetadata) -> Any:
        return json.loads(file.read())["data"]

    def write(self, data: Any, file: IO):
        json.dump({"data": data}, file, cls=DjangoJSONEncoder)

    def get_metadata(self, data: Any) -> ResourceMetadata:
        return ResourceMetadata(type=type(data).__name__, format="json", data_schema={})


class DataFramesSerializer(Serializer):
    supported_types = [pd.DataFrame]

    def read(self, file: IO, metadata: ResourceMetadata) -> Any:
        date_cols = [field["name"] for field in metadata.data_schema["fields"] if field["type"] == "datetime"]
        return pd.read_json(file, orient="records", lines=True, convert_dates=date_cols)

    def write(self, data: Any, file: IO):
        data.to_json(file, orient="records", lines=True, date_format="iso")

    def get_metadata(self, data: Any) -> ResourceMetadata:
        return ResourceMetadata(
            type="dataframe",
            format="jsonl",
            data_schema=pd.io.json.build_table_schema(data),
        )


def get_serializer(data: Any) -> Serializer:
    for serializer in [BasicTypeSerializer, DataFramesSerializer]:
        if type(data) in serializer.supported_types:
            return serializer()
    raise NotImplementedError(f"No serializer found for {type(data)}")
