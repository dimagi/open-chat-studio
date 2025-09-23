from django.conf import settings
from django_tables2 import columns, tables

from apps.experiments.models import Participant


class ParticipantTable(tables.Table):
    channel = columns.Column(accessor="get_platform_display", order_by="platform")
    created_at = columns.DateTimeColumn(verbose_name="Created On", format="Y-m-d H:i:s")

    class Meta:
        model = Participant
        fields = ("name", "channel", "identifier", "created_at", "remote_id")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        empty_text = "No participants found."
        order_by = ("-created_at", "name", "identifier")
