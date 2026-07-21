import importlib
import json
from urllib.parse import urlencode

import pytest
from django.apps import apps
from django.db import connection
from django.http import QueryDict

from apps.utils.factories.evaluations import DatasetAutoPopulationRuleFactory
from apps.web.dynamic_filters.datastructures import FilterParams

_migration = importlib.import_module("apps.evaluations.migrations.0016_migrate_auto_population_filter_query_strings")
migrate_auto_population_filter_query_strings = _migration.migrate_auto_population_filter_query_strings


class FakeSchemaEditor:
    connection = connection


def _run_migration():
    migrate_auto_population_filter_query_strings(apps, FakeSchemaEditor())


@pytest.mark.django_db()
def test_migrates_legacy_filter_to_new_format():
    legacy = urlencode(
        {
            "filter_0_column": "status",
            "filter_0_operator": "equals",
            "filter_0_value": "active",
        }
    )
    rule = DatasetAutoPopulationRuleFactory(filter_query_string=legacy)

    _run_migration()

    rule.refresh_from_db()
    assert dict(QueryDict(rule.filter_query_string).items()) == {
        "f_status": "active",
        "op_status": "equals",
    }


@pytest.mark.django_db()
def test_migration_round_trips_separator_in_value():
    """A ~-containing value must survive the migration and parse back to the original list."""
    legacy = urlencode(
        {
            "filter_0_column": "tags",
            "filter_0_operator": "any of",
            "filter_0_value": '["tag~2", "a"]',
        }
    )
    rule = DatasetAutoPopulationRuleFactory(filter_query_string=legacy)

    _run_migration()

    rule.refresh_from_db()
    query = QueryDict(rule.filter_query_string)
    assert query["f_tags"] == '"tag~2"~a'
    filter_params = FilterParams(query)
    assert json.loads(filter_params.get("tags").value) == ["tag~2", "a"]


@pytest.mark.django_db()
def test_migration_leaves_new_format_unchanged():
    """Already-migrated strings, even leading with a non-filter param, are not touched."""
    already_new = "page=2&f_tags=x&op_tags=any+of"
    rule = DatasetAutoPopulationRuleFactory(filter_query_string=already_new)

    _run_migration()

    rule.refresh_from_db()
    assert rule.filter_query_string == already_new


@pytest.mark.django_db()
def test_migration_skips_empty_query_string():
    rule = DatasetAutoPopulationRuleFactory(filter_query_string="")

    _run_migration()

    rule.refresh_from_db()
    assert rule.filter_query_string == ""
