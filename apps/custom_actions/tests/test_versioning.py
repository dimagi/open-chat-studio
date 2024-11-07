import pytest

from apps.custom_actions.models import CustomAction, CustomActionOperation

ACTION_SCHEMA = {
    "openapi": "3.0.0",
    "info": {"title": "Weather API", "version": "1.0.0"},
    "servers": [{"url": "https://api.weather.com"}],
    "paths": {
        "/weather": {
            "get": {
                "summary": "Get weather",
                "parameters": [
                    {"$ref": "#/components/parameters/Location"},
                ],
            },
            "post": {
                "summary": "Update weather",
            },
        },
        "/pollen": {
            "get": {
                "summary": "Get pollen count",
            }
        },
    },
    "components": {
        "parameters": {
            "Location": {
                "name": "location",
                "in": "query",
                "required": True,
                "schema": {"type": "string"},
                "description": "The location to get the weather for",
            },
        }
    },
}

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
def custom_action(experiment):
    return CustomAction.objects.create(
        team=experiment.team,
        name="Custom Action",
        description="Custom action description",
        prompt="Custom action prompt",
        api_schema=ACTION_SCHEMA,
    )


@pytest.mark.django_db()
def test_versioning(custom_action, experiment):
    assert experiment.is_working_version
    weather_get = CustomActionOperation.objects.create(
        custom_action=custom_action, experiment=experiment, operation_id="weather_get"
    )
    pollen_get = CustomActionOperation.objects.create(
        custom_action=custom_action, experiment=experiment, operation_id="pollen_get"
    )
    assert weather_get.is_working_version
    # working version has no saved schema
    assert weather_get._operation_schema == {}
    # working version computes schema when needed
    assert weather_get.operation_schema == EXPECTED_WEATHER_GET_SCHEMA
    # make sure the schema still isn't persisted
    assert weather_get._operation_schema == {}

    assert pollen_get.is_working_version
    assert pollen_get._operation_schema == {}
    assert pollen_get.operation_schema == EXPECTED_POLLEN_GET_SCHEMA

    experiment2 = experiment.create_new_version()
    assert experiment2.is_a_version

    weather_get2 = experiment2.custom_action_operations.get(operation_id="weather_get")
    assert weather_get2.is_a_version
    # versioned operation stores the saves standalone the schema in the DB
    assert weather_get2._operation_schema == EXPECTED_WEATHER_GET_SCHEMA
    assert weather_get2.operation_schema == EXPECTED_WEATHER_GET_SCHEMA

    pollen_get2 = experiment2.custom_action_operations.get(operation_id="pollen_get")
    assert pollen_get2.is_a_version
    assert pollen_get2._operation_schema == EXPECTED_POLLEN_GET_SCHEMA
    assert pollen_get2.operation_schema == EXPECTED_POLLEN_GET_SCHEMA
