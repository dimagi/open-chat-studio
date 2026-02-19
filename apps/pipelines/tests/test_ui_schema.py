from pydantic import BaseModel, Field

from apps.pipelines.nodes.base import UiSchema, VisibleWhen, Widgets


class ModelWithVisibleWhen(BaseModel):
    toggle_field: bool = Field(
        default=False,
        json_schema_extra=UiSchema(widget=Widgets.toggle),
    )
    dependent_field: str = Field(
        default="",
        json_schema_extra=UiSchema(visible_when=VisibleWhen(field="toggle_field", value=True)),
    )
    ne_field: str = Field(
        default="",
        json_schema_extra=UiSchema(visible_when=VisibleWhen(field="toggle_field", value=False, operator="!=")),
    )
    in_field: str = Field(
        default="",
        json_schema_extra=UiSchema(visible_when=VisibleWhen(field="toggle_field", value=[True, False], operator="in")),
    )
    not_in_field: str = Field(
        default="",
        json_schema_extra=UiSchema(visible_when=VisibleWhen(field="toggle_field", value=[True], operator="not_in")),
    )
    multi_condition_field: str = Field(
        default="",
        json_schema_extra=UiSchema(
            visible_when=[
                VisibleWhen(field="toggle_field", value=True),
                VisibleWhen(field="dependent_field", value="active"),
            ]
        ),
    )


def get_schema_properties():
    return ModelWithVisibleWhen.model_json_schema()["properties"]


def test_visible_when_single_condition_equality():
    properties = get_schema_properties()
    assert properties["dependent_field"]["ui:visibleWhen"] == {
        "field": "toggle_field",
        "value": True,
        "operator": "==",
    }


def test_visible_when_single_condition_inequality():
    properties = get_schema_properties()
    assert properties["ne_field"]["ui:visibleWhen"] == {
        "field": "toggle_field",
        "value": False,
        "operator": "!=",
    }


def test_visible_when_single_condition_in():
    properties = get_schema_properties()
    assert properties["in_field"]["ui:visibleWhen"] == {
        "field": "toggle_field",
        "value": [True, False],
        "operator": "in",
    }


def test_visible_when_single_condition_not_in():
    properties = get_schema_properties()
    assert properties["not_in_field"]["ui:visibleWhen"] == {
        "field": "toggle_field",
        "value": [True],
        "operator": "not_in",
    }


def test_visible_when_multiple_conditions():
    properties = get_schema_properties()
    assert properties["multi_condition_field"]["ui:visibleWhen"] == [
        {"field": "toggle_field", "value": True, "operator": "=="},
        {"field": "dependent_field", "value": "active", "operator": "=="},
    ]


def test_no_visible_when_when_not_set():
    properties = get_schema_properties()
    assert "ui:visibleWhen" not in properties["toggle_field"]


def test_visible_when_default_operator():
    condition = VisibleWhen(field="my_field", value="my_value")
    assert condition.operator == "=="
