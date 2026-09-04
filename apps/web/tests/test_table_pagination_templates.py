"""The pager's "X of TOTAL" is only replaced by "Page N" for lazily paginated tables.

`render_table` renders its template with a fresh context, so a `{% render_table ... with
lazy_pagination=True %}` never reaches the template; the two variants must differ by
template, not by a context flag.
"""

import django_tables2 as tables
import pytest
from django.template.loader import render_to_string
from django_tables2 import LazyPaginator, RequestConfig

ROWS = [{"name": f"row-{i}"} for i in range(5)]


class SimpleTable(tables.Table):
    name = tables.Column()


pytestmark = pytest.mark.django_db()


def render(template_name, request, **paginate):
    table = SimpleTable(ROWS)
    RequestConfig(request, paginate={"per_page": 2, **paginate}).configure(table)
    return " ".join(render_to_string(template_name, {"table": table}, request=request).split())


def test_standard_table_pager_shows_the_total(rf):
    assert "2 of 5" in render("table/single_table.html", rf.get("/"))


def test_lazy_table_pager_shows_only_the_page_number(rf):
    html = render("table/single_table_lazy_pagination.html", rf.get("/"), paginator_class=LazyPaginator)

    assert "Page 1" in html
    assert "of 5" not in html


def test_lazy_pager_never_asks_for_a_count(rf):
    """`LazyPaginator.count` raises, so rendering the total would break the page."""
    table = SimpleTable(ROWS)
    RequestConfig(rf.get("/"), paginate={"per_page": 2, "paginator_class": LazyPaginator}).configure(table)

    with pytest.raises(NotImplementedError):
        table.paginator.count  # noqa: B018
