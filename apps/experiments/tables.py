from django.conf import settings
from django_tables2 import columns, tables

from apps.experiments.models import SafetyLayer, SourceMaterial


class SafetyLayerTable(tables.Table):
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        extra_context={
            "edit_url_name": "experiments:safety_edit",
            "delete_url_name": "experiments:safety_delete",
        },
    )

    class Meta:
        model = SafetyLayer
        fields = (
            "prompt",
            "messages_to_review",
            "actions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No safety layers found."


class SourceMaterialTable(tables.Table):
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        extra_context={
            "edit_url_name": "experiments:source_material_edit",
            "delete_url_name": "experiments:source_material_delete",
        },
    )

    class Meta:
        model = SourceMaterial
        fields = (
            "topic",
            "description",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No source material found."
