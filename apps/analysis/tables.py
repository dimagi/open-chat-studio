from django.conf import settings
from django_tables2 import columns, tables

from apps.analysis.models import Analysis


class AnalysisTable(tables.Table):
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        extra_context={
            "edit_url_name": "analysis:edit",
            "delete_url_name": "analysis:delete",
        },
    )

    class Meta:
        model = Analysis
        fields = ("name",)
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No pipelines found."
