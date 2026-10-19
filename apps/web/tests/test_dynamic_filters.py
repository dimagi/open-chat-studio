import pytest

from apps.experiments.filters import ChatMessageFilter, ExperimentSessionFilter
from apps.web.dynamic_filters.base import (
    FIELD_TYPE_FILTERS,
    ChoiceColumnFilter,
    MultiColumnFilter,
    StringColumnFilter,
    YesNoExistsFilter,
    get_filter_registry,
    get_filter_schema,
)


class _TestFilter(MultiColumnFilter):
    slug = "test_slug"
    filters = [
        StringColumnFilter(
            query_param="name",
            label="Name",
            columns=["name_col"],
            description="Filter by name",
        ),
    ]


class _ChoiceTestFilter(MultiColumnFilter):
    filters = [
        ChoiceColumnFilter(query_param="labelled", label="Labelled", options=[{"id": "true", "label": "Blocked"}]),
        ChoiceColumnFilter(query_param="plain", label="Plain", options=["Pending", "Completed"]),
        ChoiceColumnFilter(query_param="dynamic", label="Dynamic"),
    ]


class TestGetFilterSchema:
    def test_extracts_schema(self):
        schema = get_filter_schema(_TestFilter)
        assert "name" in schema
        assert schema["name"]["label"] == "Name"
        assert schema["name"]["type"] == "string"
        assert schema["name"]["description"] == "Filter by name"
        assert schema["name"]["operators"] == [op.value for op in FIELD_TYPE_FILTERS["string"]]

    def test_schema_keys_are_query_params(self):
        schema = get_filter_schema(_TestFilter)
        assert list(schema.keys()) == ["name"]

    def test_choice_options_keep_their_labels(self):
        schema = get_filter_schema(_ChoiceTestFilter)
        assert schema["labelled"]["options"] == [{"id": "true", "label": "Blocked"}]
        assert schema["plain"]["options"] == ["Pending", "Completed"]
        assert "options" not in schema["dynamic"]


class TestGetFilterRegistry:
    def test_includes_slugged_subclasses(self):
        registry = get_filter_registry()
        assert "test_slug" in registry
        assert registry["test_slug"] is _TestFilter

    def test_excludes_unslugged_subclasses(self):
        registry = get_filter_registry()
        for slug in registry:
            assert slug != ""


class TestExperimentSessionFilterSchema:
    def test_schema_has_all_columns(self):

        schema = get_filter_schema(ExperimentSessionFilter)
        expected_keys = {
            "participant",
            "last_activity",
            "last_message",
            "first_message",
            "message_date",
            "tags",
            "versions",
            "channels",
            "experiment",
            "state",
            "remote_id",
            "session_id",
            "annotation_queue",
            "moderation_status",
            "rule_hit",
            "participant_blocked",
        }
        assert set(schema.keys()) == expected_keys

    def test_all_columns_have_descriptions(self):

        schema = get_filter_schema(ExperimentSessionFilter)
        for key, col in schema.items():
            assert col["description"], f"Column {key!r} has no description"

    def test_timestamp_labels_distinguish_activity_from_participant_messages(self):
        """``last_activity`` covers sessions with no participant message at all, so the
        message-based filters must not be labelled as if they were the general case."""
        schema = get_filter_schema(ExperimentSessionFilter)
        assert schema["last_activity"]["label"] == "Last Activity"
        assert schema["last_message"]["label"] == "Last Participant Message"
        assert schema["first_message"]["label"] == "First Participant Message"


class TestChatMessageFilterSchema:
    def test_schema_has_all_columns(self):

        schema = get_filter_schema(ChatMessageFilter)
        expected_keys = {"tags", "last_message", "versions"}
        assert set(schema.keys()) == expected_keys


class _YesNoFilter(YesNoExistsFilter):
    query_param: str = "yes_no"
    label: str = "Yes no"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param({}, [{"id": "true", "label": "Yes"}, {"id": "false", "label": "No"}], id="default"),
        pytest.param({"options": []}, [], id="explicit-empty"),
        pytest.param({"options": [{"id": "true", "label": "Y"}]}, [{"id": "true", "label": "Y"}], id="explicit"),
    ],
)
def test_yes_no_filter_builds_default_options_only_when_none_are_given(kwargs, expected):
    assert _YesNoFilter(**kwargs).options == expected
