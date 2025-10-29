import django_tables2 as tables
from django.template.loader import get_template
from django.utils.html import format_html


class ColumnWithHelp(tables.Column):
    def __init__(self, help_text=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help_text = help_text
        self.extra_context = kwargs.get("extra_context", {})

    def header(self):
        kwargs = {
            "verbose_name": self.verbose_name,
            "help": "",
            "checkbox": "",
        }

        if self.help_text:
            kwargs["help"] = get_template("generic/help.html").render({"help_content": self.help_text})

        if table_header := self.extra_context.get("table_header"):
            kwargs["checkbox"] = get_template("evaluations/session_checkbox.html").render(table_header)

        return format_html("""{checkbox}<span>{verbose_name}</span>{help}""", **kwargs)


class TemplateColumnWithHelp(ColumnWithHelp, tables.TemplateColumn):
    """A TemplateColumn that supports help text in the header."""

    pass


class TimeAgoColumn(tables.TemplateColumn):
    """
    A column that renders `datetime` instances using the `naturaltime` filter.
    """

    def __init__(self, *args, **kwargs):
        template = """
        {% load humanize %}
        <time datetime="{{ value.isoformat }}" title="{{ value|date:"SHORT_DATETIME_FORMAT" }}">
            {{ value|naturaltime|default:default}}
        </time>
        """
        super().__init__(template_code=template, *args, **kwargs)  # noqa B026
