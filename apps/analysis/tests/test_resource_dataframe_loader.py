from unittest.mock import MagicMock

import pandas as pd
import pytest
from django.core.files.base import ContentFile

from apps.analysis.core import PipelineContext
from apps.analysis.models import Resource
from apps.analysis.steps.loaders import ResourceDataframeLoader, ResourceLoaderParams, ResourceType


@pytest.fixture
def resource_dataframe_loader():
    step = ResourceDataframeLoader()
    step.initialize(PipelineContext())
    return step


def make_resource(resource_type, content):
    resource = MagicMock(spec=Resource)
    resource.type = resource_type
    resource.file = ContentFile(content)
    return resource


def get_params(resource):
    params = ResourceLoaderParams()
    params.resource_id = 1
    # fake the cached property
    params.__dict__["resource"] = resource
    return params


@pytest.mark.parametrize(
    "resource_type,raw_data,expected",
    [
        (ResourceType.CSV, "a,b,c\n1,2,3\n4,5,6", "a,b,c\n1,2,3\n4,5,6\n"),
        (
            ResourceType.JSONL,
            """{"a": 1, "b": 2, "c": {"d": 3}}\n{"a": 4, "b": 5, "c": {"d": 6}}""",
            "a,b,c.d\n1,2,3\n4,5,6\n",
        ),
        (ResourceType.JSON, """{"a": 1, "b": 2, "c": 3}""", "a,b,c\n1,2,3\n"),
        (ResourceType.TEXT, "This is some text\nAnd some more text", "line\nThis is some text\nAnd some more text\n"),
    ],
)
def test_resource_dataframe_loader(resource_type, raw_data, expected, resource_dataframe_loader):
    resource = make_resource(resource_type, raw_data)
    params = get_params(resource)
    result = resource_dataframe_loader.load(params)
    assert isinstance(result.data, pd.DataFrame)
    assert result.data.to_csv(index=False) == expected


def test_resource_dataframe_loader_raises_error_with_invalid_resource_type(resource_dataframe_loader):
    resource = make_resource("invalid", "")
    params = get_params(resource)
    with pytest.raises(ValueError):
        resource_dataframe_loader.load(params)
