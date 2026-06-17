import logging

from django.core.management.base import BaseCommand

from apps.pipelines.models import Node

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill resource FK fields on Node from params JSON. Idempotent."

    def handle(self, *args, **options):
        # get_all() bypasses the default manager's is_archived=False filter so the mirror is
        # backfilled on every node — working versions, published versions, and soft-deleted
        # (archived) versions alike. Archived versions still hold references that the mirror must reflect.
        nodes = Node.objects.get_all()
        total = nodes.count()
        self.stdout.write(f"Backfilling FK fields for {total} nodes...")
        processed = errors = 0

        for node in nodes.iterator():
            try:
                node._sync_resource_fk_fields()
            except Exception:
                logger.exception("Failed FK backfill for node pk=%s flow_id=%s", node.pk, node.flow_id)
                errors += 1
                continue
            processed += 1

        self.stdout.write(self.style.SUCCESS(f"Done. Processed: {processed}, errors: {errors}"))
