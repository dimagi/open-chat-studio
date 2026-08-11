"""JSON Schema / OpenAPI helpers shared across apps."""

from copy import deepcopy


def resolve_references(openapi_spec: dict) -> dict:
    """Returns a copy of `openapi_spec` with every internal `$ref` replaced by what it points at."""

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
