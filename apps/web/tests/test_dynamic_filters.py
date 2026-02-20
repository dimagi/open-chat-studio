from apps.web.dynamic_filters.base import (
    FIELD_TYPE_FILTERS,
    ColumnFilter,
    MultiColumnFilter,
    StringColumnFilter,
    get_filter_registry,
    get_filter_schema,
)


class TestColumnFilterDescription:
    def test_default_description_is_empty(self):
        f = ColumnFilter(query_param="test", label="Test", type="string")
        assert f.description == ""

    def test_description_can_be_set(self):
        f = ColumnFilter(query_param="test", label="Test", type="string", description="A test filter")
        assert f.description == "A test filter"


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
