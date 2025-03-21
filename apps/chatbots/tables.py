import django_tables2 as tables
from django.conf import settings
from django_tables2 import columns

from apps.experiments.models import Experiment


class ChatbotTable(tables.Table):
    name = columns.Column(
        orderable=True,
    )
    description = columns.Column(verbose_name="Description")
    owner = columns.Column(accessor="owner__username", verbose_name="Created By")
    actions = columns.TemplateColumn(
        template_name="experiments/components/experiment_actions_column.html",
    )

    class Meta:
        model = Experiment
        fields = ("name",)
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
            "data-redirect-url": lambda record: (
                record.get_absolute_url() if hasattr(record, "get_absolute_url") else ""
            ),
        }
        orderable = False
        empty_text = "No experiments found."

    def render_name(self, record):
        if record.is_archived:
            return f"{record.name} (archived)"
        return record.name
