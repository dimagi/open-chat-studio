import importlib
import json
from urllib.parse import urlencode

import pytest
from django.apps import apps
from django.db import connection
from django.http import QueryDict

from apps.filters.models import FilterSet
from apps.utils.factories.user import UserFactory
from apps.web.dynamic_filters.datastructures import FilterParams

_migration = importlib.import_module("apps.filters.migrations.0004_migrate_saved_filters_format")
migrate_saved_filters = _migration.migrate_saved_filters


class FakeSchemaEditor:
    connection = connection


def _run_migration():
    migrate_saved_filters(apps, FakeSchemaEditor())


def _make_filter_set(team, query_string):
    return FilterSet.objects.create(
        team=team,
        user=UserFactory(),
        name="test",
        table_type=FilterSet.TableType.SESSIONS,
        filter_query_string=query_string,
    )


@pytest.mark.django_db()
def test_migrates_legacy_filter_to_new_format(team):
    legacy = urlencode(
        {
            "filter_0_column": "status",
            "filter_0_operator": "equals",
            "filter_0_value": "active",
        }
    )
    filter_set = _make_filter_set(team, legacy)

    _run_migration()

    filter_set.refresh_from_db()
    assert dict(QueryDict(filter_set.filter_query_string).items()) == {
        "f_status": "active",
        "op_status": "equals",
    }


@pytest.mark.django_db()
def test_migration_round_trips_separator_in_value(team):
    """A ~-containing value must survive the migration and parse back to the original list."""
    legacy = urlencode(
        {
            "filter_0_column": "tags",
            "filter_0_operator": "any of",
            "filter_0_value": '["tag~2", "a"]',
        }
    )
    filter_set = _make_filter_set(team, legacy)

    _run_migration()

    filter_set.refresh_from_db()
    query = QueryDict(filter_set.filter_query_string)
    assert query["f_tags"] == '"tag~2"~a'
    filter_params = FilterParams(query)
    assert json.loads(filter_params.get("tags").value) == ["tag~2", "a"]


@pytest.mark.django_db()
def test_migration_leaves_new_format_unchanged(team):
    """Already-migrated strings, even leading with a non-filter param, are not touched."""
    already_new = "page=2&f_tags=x&op_tags=any+of"
    filter_set = _make_filter_set(team, already_new)

    _run_migration()

    filter_set.refresh_from_db()
    assert filter_set.filter_query_string == already_new


@pytest.mark.django_db()
def test_migration_skips_empty_query_string(team):
    filter_set = _make_filter_set(team, "")

    _run_migration()

    filter_set.refresh_from_db()
    assert filter_set.filter_query_string == ""
