from django.conf import settings
from django_tables2 import columns, tables

from apps.generics import actions
from apps.pipelines.models import Pipeline


class PipelineTable(tables.Table):
    name = columns.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="pipelines:edit"),
            actions.AjaxAction(
                "pipelines:delete",
                title="Archive",
                icon_class="fa-solid fa-box-archive",
                required_permissions=["pipelines.delete_pipeline"],
                confirm_message="This will delete the pipeline and any associated logs. Are you sure?",
                hx_method="delete",
            ),
        ]
    )

    class Meta:
        model = Pipeline
        fields = (
            "name",
            "actions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No pipelines found."
