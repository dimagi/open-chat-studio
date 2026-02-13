import pytest

from apps.custom_actions.models import CustomActionOperation
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory

EXPECTED_POLLEN_GET_SCHEMA = {
    "openapi": "3.0.0",
    "info": {
        "title": "Weather API - get /pollen",
        "version": "1.0.0",
        "description": "Standalone OpenAPI spec for get /pollen",
    },
    "servers": [{"url": "https://api.weather.com"}],
    "paths": {
        "/pollen": {
            "get": {
                "summary": "Get pollen count",
            }
        },
    },
}

EXPECTED_WEATHER_GET_SCHEMA = {
    "openapi": "3.0.0",
    "info": {
        "title": "Weather API - get /weather",
        "version": "1.0.0",
        "description": "Standalone OpenAPI spec for get /weather",
    },
    "servers": [{"url": "https://api.weather.com"}],
    "paths": {
        "/weather": {
            "get": {
                "summary": "Get weather",
                "parameters": [
                    {
                        "name": "location",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "The location to get the weather for",
                    }
                ],
            },
        },
    },
}


@pytest.fixture()
def custom_action():
    return CustomActionFactory()


@pytest.mark.django_db()
def test_versioning_with_node(custom_action):
    """Test that the custom actions are also versioned when versioning the pipeline"""
    pipeline = PipelineFactory()
    node = NodeFactory(pipeline=pipeline, type="LLMResponseWithPrompt", params={"custom_actions": ["weather_get"]})
    weather_get = CustomActionOperation.objects.create(
        custom_action=custom_action, node=node, operation_id="weather_get"
    )
    pipeline.create_new_version()

    assert node.versions.count() == 1
    assert weather_get.versions.count() == 1
    assert weather_get.versions.first().node == node.versions.first()
