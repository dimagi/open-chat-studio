from django.conf import settings
from django.utils.html import format_html
from django_tables2 import columns, tables

from .models import TranscriptAnalysis


class TranscriptAnalysisTable(tables.Table):
    name = columns.Column(linkify=True)
    experiment = columns.Column(verbose_name="Experiment Name", accessor="experiment__name")
    status = columns.Column()
    created_at = columns.DateTimeColumn(verbose_name="Created")
    actions = columns.TemplateColumn(
        template_name="analysis/components/actions_column.html", verbose_name="Actions", orderable=False
    )

    class Meta:
        model = TranscriptAnalysis
        fields = ("name", "experiment", "status", "created_at")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        attrs = {
            "class": "table table-hover",
        }
        orderable = True
        empty_text = "No transcript analyses found."

    def render_status(self, value):
        status_colors = {
            "pending": "bg-yellow-100 text-yellow-800",
            "processing": "bg-blue-100 text-blue-800",
            "completed": "bg-green-100 text-green-800",
            "failed": "bg-red-100 text-red-800",
        }
        color_class = status_colors.get(value, "bg-gray-100")
        return format_html('<span class="px-2 py-1 rounded-full {}">{}</span>', color_class, value.capitalize())
