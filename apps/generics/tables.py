import django_tables2 as tables
from django.template import Context, Template
from django.template.loader import get_template
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html


class ColumnWithHelp(tables.Column):
    def __init__(self, help_text=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help_text = help_text

    def header(self):
        if not self.help_text:
            return self.verbose_name
        help_html = get_template("generic/help.html").render({"help_content": self.help_text})
        return format_html("""<span>{header}</span>{help}""", header=self.verbose_name, help=help_html)


class ColumnWithCustomHeader(tables.Column):
    def __init__(self, header_template=None, header_context=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.template_path = header_template
        self.template_context = header_context or {}

    def header(self):
        context = {"verbose_name": self.verbose_name, **self.template_context}
        return get_template(self.template_path).render(context)


class TemplateColumnWithCustomHeader(ColumnWithCustomHeader, tables.TemplateColumn):
    """A Template column that allows custom templates for the column header."""


class TemplateColumnWithHelp(ColumnWithHelp, tables.TemplateColumn):
    """A TemplateColumn that supports help text in the header."""


class TimeAgoColumn(tables.TemplateColumn):
    """
    A column that renders `datetime` instances using the `naturaltime` filter.
    """

    TEMPLATE = """
    {% load humanize %}
    <time datetime="{{ value.isoformat }}" title="{{ value|date:"SHORT_DATETIME_FORMAT" }}">
        {{ value|naturaltime|default:default}}
    </time>
    """

    _compiled_template = None

    def __init__(self, *args, **kwargs):
        super().__init__(template_code=self.TEMPLATE, *args, **kwargs)  # noqa B026

    @classmethod
    def _get_template(cls):
        if cls._compiled_template is None:
            cls._compiled_template = Template(cls.TEMPLATE)
        return cls._compiled_template

    def render(self, value, bound_column=None, **kwargs):  # ty: ignore[invalid-method-override]
        # Render with a plain Context rather than the table's RequestContext. Rendering a
        # template against a RequestContext re-runs every context processor (DB/cache hits)
        # for each cell, which causes an N+1 across the table rows.
        context = {
            "value": value,
            "default": bound_column.default if bound_column is not None else self.default,
            **self.extra_context,
        }
        return self._get_template().render(Context(context))


class ISOTimeAgoColumn(TimeAgoColumn):
    """A TimeAgoColumn that parses ISO datetime strings before rendering."""

    def render(self, value, **kwargs):  # ty: ignore[invalid-method-override]
        if isinstance(value, str):
            value = parse_datetime(value)
        return super().render(value=value, **kwargs)


class ArrayColumn(tables.Column):
    """
    A column that renders `array` fields.
    """

    def render(self, value):
        return ", ".join([str(v) for v in value]) if value else ""
