from django.conf import settings
from django_tables2 import columns, tables

from apps.services.models import ServiceConfig


class ServiceConfigTable(tables.Table):
    subtype = columns.Column(verbose_name="Type")
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        # extra_context={
        #     "edit_url_name": "services:edit",
        #     "delete_url_name": "services:delete",
        # },
    )

    class Meta:
        model = ServiceConfig
        fields = (
            "subtype",
            "name",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
