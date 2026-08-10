"""JSON Schema / OpenAPI helpers shared across apps.

Lives in `utils` rather than any one app because four apps read it: `custom_actions` (user-supplied
OpenAPI specs), `pipelines` and `evaluations` (pydantic `model_json_schema()` output) and
`service_providers` (LLM model parameter schemas).
"""

from copy import deepcopy


def resolve_references(openapi_spec: dict) -> dict:
    """
    Resolves all $ref references in an OpenAPI specification document.

    Args:
        openapi_spec: The OpenAPI specification document.

    Returns:
        The OpenAPI specification document with all $ref references resolved.
    """

    def resolve_ref(data: dict, path: str) -> dict:
        if "$ref" in data:
            ref = data["$ref"]
            if not ref[0] == "#":
                raise ValueError(f"External references are not supported: {ref}")

            ref_path = ref[1:].split("/")[1:]
            current = openapi_spec
            for p in ref_path:
                current = current[p]
            # preserve metadata fields
            extra = deepcopy(data)
            extra.pop("$ref")
            return {**deepcopy(current), **extra}
        elif isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict | list):
                    data[k] = resolve_ref(v, f"{path}/{k}")
                else:
                    data[k] = v
        elif isinstance(data, list):
            for i, item in enumerate(data):
                if isinstance(item, dict | list):
                    data[i] = resolve_ref(item, f"{path}/{i}")
                else:
                    data[i] = item
        return data

    return resolve_ref(deepcopy(openapi_spec), "")
