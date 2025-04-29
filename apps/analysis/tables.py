from django.utils.html import format_html
from django_tables2 import columns, tables

from .models import TranscriptAnalysis


class TranscriptAnalysisTable(tables.Table):
    name = columns.Column(linkify=True)
    experiment = columns.Column(accessor="experiment.name")
    status = columns.Column()
    created_at = columns.DateTimeColumn(verbose_name="Created")
    actions = columns.TemplateColumn(
        template_name="analysis/components/actions_column.html", verbose_name="Actions", orderable=False
    )

    class Meta:
        model = TranscriptAnalysis
        fields = ("name", "experiment", "status", "created_at")
        attrs = {
            "class": "table table-hover",
        }
        row_attrs = {
            "class": "border-b hover:bg-gray-50",
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
        return format_html('<span class="px-2 py-1 rounded-full text-xs {}">{}</span>', color_class, value.capitalize())
