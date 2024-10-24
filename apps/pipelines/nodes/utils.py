import pydantic


def get_input_types_for_node(node_class):
    class InputParam(pydantic.BaseModel):
        name: str
        type: str

    class NodeInputType(pydantic.BaseModel):
        name: str
        human_name: str
        input_params: list[InputParam]
        node_description: str

    inputs = []
    for field_name, info in node_class.model_fields.items():
        if getattr(info.annotation, "_name", None) == "Optional":
            type_ = info.annotation.__args__[0]
        else:
            type_ = info.annotation
        new_input = InputParam(name=field_name, type=str(type_))
        inputs.append(new_input)

    return NodeInputType(
        name=node_class.__name__,
        human_name=getattr(node_class, "__human_name__", node_class.__name__),
        input_params=inputs,
        node_description=getattr(node_class, "__node_description__", ""),
    ).model_dump()
