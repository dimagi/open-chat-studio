import django_tables2 as tables
from django.template.loader import get_template
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
