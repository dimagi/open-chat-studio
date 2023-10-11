from django.conf import settings
from django_tables2 import tables

from apps.experiments.models import SafetyLayer


class SafetyLayerTable(tables.Table):
    class Meta:
        model = SafetyLayer
        fields = (
            "prompt",
            "messages_to_review",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No safety layers found."
