import factory
import factory.django

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


class CustomActionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "custom_actions.CustomAction"

    team = factory.SubFactory("apps.utils.factories.team.TeamFactory")
    name = "Custom Action"
    description = "Custom action description"
    prompt = "Custom action prompt"
    api_schema = ACTION_SCHEMA
    allowed_operations = ["weather_get"]
    server_url = "https://api.weather.com"
