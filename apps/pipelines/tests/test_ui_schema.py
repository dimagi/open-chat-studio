from pydantic import BaseModel, ConfigDict, Field

from apps.pipelines.nodes.base import NodeSchema, PipelineNode, UiSchema, VisibleWhen, Widgets
from apps.pipelines.nodes.mixins import HistoryMixin


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


def test_visible_when_is_not_empty():
    class TestModel(PipelineNode):
        model_config = ConfigDict(json_schema_extra=NodeSchema(label="Test"))
        items: list[int] = Field(
            default_factory=list,
            json_schema_extra=UiSchema(visible_when=VisibleWhen(field="items", operator="is_not_empty")),
        )

    schema = TestModel.model_json_schema()
    assert schema["properties"]["items"]["ui:visibleWhen"] == {
        "field": "items",
        "operator": "is_not_empty",
        "value": None,
    }


def test_visible_when_is_empty():
    class TestModel(PipelineNode):
        model_config = ConfigDict(json_schema_extra=NodeSchema(label="Test"))
        items: list[int] = Field(
            default_factory=list,
            json_schema_extra=UiSchema(visible_when=VisibleWhen(field="items", operator="is_empty")),
        )

    schema = TestModel.model_json_schema()
    assert schema["properties"]["items"]["ui:visibleWhen"] == {
        "field": "items",
        "operator": "is_empty",
        "value": None,
    }


def test_history_mixin_user_max_token_limit_visible_when():
    props = HistoryMixin.model_json_schema()["properties"]
    assert props["user_max_token_limit"]["ui:visibleWhen"] == {
        "field": "history_mode",
        "operator": "in",
        "value": ["summarize", "truncate_tokens"],
    }
    assert "ui:widget" not in props["user_max_token_limit"]


def test_history_mixin_max_history_length_visible_when():
    props = HistoryMixin.model_json_schema()["properties"]
    assert props["max_history_length"]["ui:visibleWhen"] == {
        "field": "history_mode",
        "operator": "==",
        "value": "max_history_length",
    }
    assert "ui:widget" not in props["max_history_length"]
