import pydantic


def get_input_types_for_node(node_class):
    class InputParam(pydantic.BaseModel):
        name: str
        type: str

    class NodeInputType(pydantic.BaseModel):
        name: str
        human_name: str
        input_params: list[InputParam]

    inputs = [
        InputParam(name=field_name, type=str(info.annotation)) for field_name, info in node_class.model_fields.items()
    ]

    return NodeInputType(
        name=node_class.__name__,
        human_name=getattr(node_class, "__human_name__", node_class.__name__),
        input_params=inputs,
    ).model_dump()
