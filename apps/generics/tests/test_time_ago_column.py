from datetime import timedelta

import django_tables2 as tables
import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.template import RequestContext, Template
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_tables2 import RequestConfig

from apps.generics.actions import Action, ActionsColumn
from apps.generics.tables import ISOTimeAgoColumn, TimeAgoColumn
from apps.teams.models import Team

PROCESSOR_CALLS = []


def counting_query_processor(request):
    """Test-only context processor that hits the DB, like `user_teams` does in production."""
    PROCESSOR_CALLS.append(request)
    Team.objects.exists()
    return {}


TEST_TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": settings.TEMPLATES[0]["DIRS"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                f"{__name__}.counting_query_processor",
            ],
        },
    }
]


class ActionsTable(tables.Table):
    name = tables.Column()
    actions = ActionsColumn(actions=[Action(url_name="open", url_factory=lambda *args: "/open/", label="Open")])


class TimeAgoTable(tables.Table):
    timestamp = TimeAgoColumn(default="never")


class ISOTimeAgoTable(tables.Table):
    timestamp = ISOTimeAgoColumn(default="never")


def _render_table(rows):
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    table = ActionsTable(data=[{"name": f"row {i}"} for i in range(rows)])
    RequestConfig(request).configure(table)
    template = Template("{% load render_table from django_tables2 %}{% render_table table %}")
    return template.render(RequestContext(request, {"table": table}))


@pytest.mark.django_db()
@override_settings(TEMPLATES=TEST_TEMPLATES)
def test_context_processors_do_not_run_per_row():
    """Rendering a `template_name` column re-ran every context processor (and its queries) once per row."""
    PROCESSOR_CALLS.clear()
    with CaptureQueriesContext(connection) as single_row:
        _render_table(rows=1)
    single_row_calls = len(PROCESSOR_CALLS)

    PROCESSOR_CALLS.clear()
    with CaptureQueriesContext(connection) as many_rows:
        _render_table(rows=10)
    many_rows_calls = len(PROCESSOR_CALLS)

    assert many_rows_calls == single_row_calls
    assert len(many_rows.captured_queries) == len(single_row.captured_queries)


def test_template_name_column_renders_without_table_context():
    """A table that was never rendered through `RequestConfig` has no `context` or `request`."""
    table = ActionsTable(data=[{"name": "row"}])

    html = table.rows[0].get_cell("actions")

    assert "/open/" in html


def test_renders_time_element():
    value = timezone.now() - timedelta(hours=2)
    table = TimeAgoTable(data=[{"timestamp": value}])

    html = table.rows[0].get_cell("timestamp")

    assert f'<time datetime="{value.isoformat()}"' in html
    assert "2\xa0hours ago" in html or "2 hours ago" in html


def test_renders_default_for_missing_value():
    table = TimeAgoTable(data=[{"timestamp": None}])

    assert "never" in table.rows[0].get_cell("timestamp")


def test_iso_column_parses_strings():
    value = timezone.now() - timedelta(hours=2)
    table = ISOTimeAgoTable(data=[{"timestamp": value.isoformat()}])

    html = table.rows[0].get_cell("timestamp")

    assert f'<time datetime="{value.isoformat()}"' in html
