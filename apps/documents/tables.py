import django_tables2 as tables
from django.conf import settings

from apps.documents.models import Collection
from apps.generics import actions
from apps.utils.conversions import bytes_to_megabytes


class CollectionsTable(tables.Table):
    # `total_bytes` is annotated on the queryset to avoid a per-row aggregate query (N+1)
    size = tables.Column(verbose_name="Total size", accessor="total_bytes", empty_values=())
    file_count = tables.Column(verbose_name="Files", empty_values=())
    actions = actions.ActionsColumn(
        actions=[
            actions.delete_action(url_name="documents:collection_delete", confirm_message="Are you sure?"),
        ]
    )

    def render_size(self, value):
        return f"{bytes_to_megabytes(value or 0)} MB"

    class Meta:
        model = Collection
        fields = ["name", "created_at", "is_index"]
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No collections found."
