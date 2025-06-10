import django_tables2 as tables
from django.conf import settings

from apps.files.models import File
from apps.generics import actions


class FilesTable(tables.Table):
    size = tables.Column(verbose_name="Size", accessor="size_mb")
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="files:file_edit"),
        ]
    )

    def render_size(self, value):
        return f"{value} MB"

    def render_summary(self, value):
        if value:
            return value[:100] + "..."
        return ""

    class Meta:
        model = File
        fields = ["name", "created_at", "content_type", "summary"]
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No files found."
