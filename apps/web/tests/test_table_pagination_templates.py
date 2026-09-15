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


def test_orderable_sort_header_is_a_real_link(rf):
    """A sort header must be reachable by keyboard, not just mouse-clickable via a bare
    `<th hx-get>` with no `tabindex` or `href` -- see #4463. The path is non-root so a
    regression to a bare relative `href` (which resolves against the *outer* embedding
    page's URL, not this fragment's own endpoint, since htmx swaps never change
    `window.location`) produces a visibly different, wrong href, not a coincidental match."""
    html = render("table/tailwind_js_pagination.html", rf.get("/members/table/"))

    assert '<a href="/members/table/?sort=name"' in html
    assert 'hx-get="/members/table/?sort=name"' in html
    assert 'hx-ext="morph"' in html
    assert 'hx-swap="morph"' in html


def test_pagination_links_carry_a_real_href(rf):
    """Same issue for prev/next: `hx-get` alone isn't keyboard-focusable, and the href
    must match hx-get's own path exactly (see above)."""
    html = render("table/tailwind_js_pagination.html", rf.get("/members/table/", {"page": "2"}))

    assert 'href="/members/table/?page=1"' in html
    assert 'hx-get="/members/table/?page=1"' in html
    assert 'href="/members/table/?page=3"' in html
    assert 'hx-get="/members/table/?page=3"' in html


def test_disabled_prev_link_has_no_href(rf):
    """Page 1 has no previous page -- the disabled link must stay inert, not gain a
    dead `href=".../?page=0"` alongside it."""
    html = render("table/tailwind_js_pagination.html", rf.get("/members/table/"))

    assert "disabled" in html
    assert "page=0" not in html
