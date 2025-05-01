import django_tables2 as tables
from django.conf import settings

from apps.documents.models import Collection
from apps.files.models import File
from apps.generics import actions


class CollectionsTable(tables.Table):
    size = tables.Column(verbose_name="Total size")
    file_count = tables.Column(verbose_name="Files", empty_values=())
    actions = actions.ActionsColumn(
        actions=[
            actions.delete_action(url_name="documents:collection_delete", confirm_message="Are you sure?"),
        ]
    )

    def render_file_count(self, record):
        return record.files.count()

    def render_size(self, value):
        return f"{value} MB"

    class Meta:
        model = Collection
        fields = ["name", "created_at", "is_index"]
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No collections found."


class FilesTable(tables.Table):
    size = tables.Column(verbose_name="Size", accessor="size_mb")
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="documents:file_edit"),
            actions.delete_action(url_name="documents:file_delete", confirm_message="Are you sure?"),
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
