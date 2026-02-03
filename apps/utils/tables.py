from django.shortcuts import render
from django.template import Context


def render_table_row(request, table_cls, record):
    table = table_cls(data=[record], request=request)
    render_first_table_row(request, table)


def render_first_table_row(request, table):
    table.context = Context({"request": request, "table": table})
    return render(request, "table/table_row.html", {"row": table.rows[0], "request": request, "table": table})
