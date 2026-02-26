from apps.web.dynamic_filters.base import (
    FIELD_TYPE_FILTERS,
    MultiColumnFilter,
    StringColumnFilter,
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


class TestGetFilterRegistry:
    def test_includes_slugged_subclasses(self):
        registry = get_filter_registry()
        assert "test_slug" in registry
        assert registry["test_slug"] is _TestFilter

    def test_excludes_unslugged_subclasses(self):
        registry = get_filter_registry()
        for slug, _cls in registry.items():
            assert slug != ""


class TestExperimentSessionFilterSchema:
    def test_schema_has_all_columns(self):
        from apps.experiments.filters import ExperimentSessionFilter

        schema = get_filter_schema(ExperimentSessionFilter)
        expected_keys = {
            "participant",
            "last_message",
            "first_message",
            "message_date",
            "tags",
            "versions",
            "channels",
            "experiment",
            "state",
            "remote_id",
        }
        assert set(schema.keys()) == expected_keys

    def test_all_columns_have_descriptions(self):
        from apps.experiments.filters import ExperimentSessionFilter

        schema = get_filter_schema(ExperimentSessionFilter)
        for key, col in schema.items():
            assert col["description"], f"Column {key!r} has no description"


class TestChatMessageFilterSchema:
    def test_schema_has_all_columns(self):
        from apps.experiments.filters import ChatMessageFilter

        schema = get_filter_schema(ChatMessageFilter)
        expected_keys = {"tags", "last_message", "versions"}
        assert set(schema.keys()) == expected_keys
