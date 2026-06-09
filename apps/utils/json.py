import json

from pydantic import BaseModel, Field, create_model


class PrettyJSONEncoder(json.JSONEncoder):
    def __init__(self, *args, **kwargs):
        # Force pretty-printing regardless of any caller-provided values.
        kwargs["indent"] = 4
        kwargs["sort_keys"] = True
        super().__init__(*args, **kwargs)


def dict_to_json_schema(data: dict) -> type[BaseModel]:
    """Converts a dictionary to a JSON schema by first converting it to a Pydantic object and dumping it again.
    The input should be in the format {"key": "description", "key2": [{"key": "description"}]}

    Nested objects are not supported at the moment

    Input example 1:
    {"name": "the user's name", "surname": "the user's surname"}

    Input example 2:
    {"name": "the user's name", "pets": [{"name": "the pet's name": "type": "the type of animal"}]}

    """

    def _create_model_from_data(value_data, model_name: str):
        pydantic_schema = {}
        for key, value in value_data.items():
            if isinstance(value, str):
                pydantic_schema[key] = (str | None, Field(description=value))
            elif isinstance(value, list):
                model = _create_model_from_data(value[0], key.capitalize())
                pydantic_schema[key] = (list[model], Field(description=f"A list of {key}"))
        return create_model(model_name, **pydantic_schema)

    Model = _create_model_from_data(data, "CustomModel")

    Model.description = ""
    return Model
