from django.conf import settings
from django_tables2 import columns, tables

from apps.experiments.models import Participant


class ParticipantTable(tables.Table):
    platform = columns.Column(accessor="get_platform_display")
    created_at = columns.DateTimeColumn(verbose_name="Created On", format="Y-m-d H:i:s")

    class Meta:
        model = Participant
        fields = ("platform", "identifier", "created_at")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        empty_text = "No participants found."
        order_by = ("-created_at", "identifier")
