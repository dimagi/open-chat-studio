import django_tables2 as tables

from .models import AnnotationItem, AnnotationQueue, AnnotationSchema


class AnnotationSchemaTable(tables.Table):
    name = tables.Column(linkify=True)
    field_count = tables.Column(verbose_name="Fields", empty_values=(), orderable=False)

    class Meta:
        model = AnnotationSchema
        fields = ["name", "description", "field_count", "created_at"]
        attrs = {"class": "table"}

    def render_field_count(self, record):
        return len(record.schema)


class AnnotationQueueTable(tables.Table):
    name = tables.Column(linkify=True)
    progress = tables.Column(verbose_name="Progress", empty_values=(), orderable=False)

    class Meta:
        model = AnnotationQueue
        fields = ["name", "schema", "status", "num_reviews_required", "progress", "created_at"]
        attrs = {"class": "table"}

    def render_progress(self, record):
        progress = record.get_progress()
        return f"{progress['completed']}/{progress['total']} ({progress['percent']}%)"


class AnnotationItemTable(tables.Table):
    item_type = tables.Column(verbose_name="Type")
    description = tables.Column(verbose_name="Description", empty_values=(), orderable=False)
    status = tables.Column()
    review_count = tables.Column(verbose_name="Reviews")

    class Meta:
        model = AnnotationItem
        fields = ["item_type", "description", "status", "review_count", "created_at"]
        attrs = {"class": "table"}

    def render_description(self, record):
        return str(record)

    def render_review_count(self, record):
        return f"{record.review_count}/{record.queue.num_reviews_required}"
