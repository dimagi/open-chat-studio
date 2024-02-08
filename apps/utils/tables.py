from django.shortcuts import render
from django.template import Context


def render_table_row(request, table_cls, record):
    table = table_cls(data=[record])
    table.context = Context({"request": request})
    return render(request, "table/table_row.html", {"row": table.rows[0], "request": request})
