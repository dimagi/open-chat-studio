from django.conf import settings
from django_tables2 import columns, tables

from .models import LlmProvider


class LlmProviderTable(tables.Table):
    actions = columns.TemplateColumn(
        template_name="service_providers/actions_column.html",
        extra_context={
            "edit_url_name": "service_providers:edit",
            "delete_url_name": "service_providers:delete",
            "provider_type": "llm",
        },
    )

    class Meta:
        model = LlmProvider
        fields = (
            "type",
            "name",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
