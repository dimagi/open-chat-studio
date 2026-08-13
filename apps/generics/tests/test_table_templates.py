import django_tables2 as tables
from django.template import Context
from django.template.loader import get_template
from django.test import RequestFactory
from django_tables2 import RequestConfig
from django_tables2.paginators import LazyPaginator


class NameTable(tables.Table):
    name = tables.Column()


def _render_table(names, *, per_page=20, lazy_pagination=False):
    request = RequestFactory().get("/")
    table = NameTable([{"name": name} for name in names])
    paginate = {"per_page": per_page}
    if lazy_pagination:
        paginate["paginator_class"] = LazyPaginator
    RequestConfig(request, paginate=paginate).configure(table)
    template = get_template("table/tailwind_js_pagination.html").template
    return template.render(Context({"table": table, "lazy_pagination": lazy_pagination, "request": request}))


def test_single_page_table_shows_record_count():
    rendered = _render_table(["one", "two", "three"])

    assert "3 records" in rendered


def test_record_count_uses_singular_label():
    rendered = _render_table(["one"])

    assert "1 record" in rendered
    assert "1 records" not in rendered


def test_empty_table_shows_zero_records():
    rendered = _render_table([])

    assert "0 records" in rendered


def test_multi_page_table_shows_total_record_count():
    rendered = _render_table(["one", "two", "three"], per_page=2)

    assert "3 records" in rendered


def test_lazy_pagination_does_not_request_total_count():
    rendered = _render_table(["one", "two", "three"], per_page=2, lazy_pagination=True)

    assert "3 records" not in rendered
    assert "Page 1" in rendered
