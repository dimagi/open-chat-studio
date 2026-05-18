from django.db import migrations
from django.utils import timezone


def forwards(apps, schema_editor):
    """Backfill the authoritative flag.

    Two operations:
    1. For each item in a queue with num_reviews_required==1 that has exactly one
       submitted annotation, mark that annotation as authoritative.
    2. For each item currently at COMPLETED in a multi-reviewer queue with no
       authoritative annotation, downgrade to AWAITING_RESOLUTION.
    """
    AnnotationItem = apps.get_model("human_annotations", "AnnotationItem")
    now = timezone.now()

    # (1) Single-reviewer auto-mark.
    for item in AnnotationItem.objects.filter(queue__num_reviews_required=1):
        submitted = list(item.annotations.filter(status="submitted"))
        if len(submitted) == 1 and not submitted[0].is_authoritative:
            ann = submitted[0]
            ann.is_authoritative = True
            ann.authoritative_set_by = None
            ann.authoritative_set_at = now
            ann.save(update_fields=["is_authoritative", "authoritative_set_by", "authoritative_set_at"])

    # (2) Multi-reviewer COMPLETED items without authoritative -> AWAITING_RESOLUTION.
    for item in AnnotationItem.objects.filter(queue__num_reviews_required__gt=1, status="completed"):
        has_auth = item.annotations.filter(is_authoritative=True).exists()
        if not has_auth:
            item.status = "awaiting_resolution"
            item.save(update_fields=["status"])


def backwards(apps, schema_editor):
    """Best-effort reverse: clear authoritative flags set by this backfill (those
    with set_by=None) and revert AWAITING_RESOLUTION to COMPLETED."""
    Annotation = apps.get_model("human_annotations", "Annotation")
    AnnotationItem = apps.get_model("human_annotations", "AnnotationItem")
    Annotation.objects.filter(is_authoritative=True, authoritative_set_by__isnull=True).update(
        is_authoritative=False, authoritative_set_at=None
    )
    AnnotationItem.objects.filter(status="awaiting_resolution").update(status="completed")


class Migration(migrations.Migration):
    dependencies = [
        ("human_annotations", "0003_authoritative_annotation_fields"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
