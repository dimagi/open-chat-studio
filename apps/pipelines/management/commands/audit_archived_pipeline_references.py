from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.pipelines.models import Node

_SCALAR_FK_FIELDS = ("assistant", "source_material", "collection")


class Command(BaseCommand):
    help = (
        "Read-only audit: list live pipeline nodes whose assistant, collection, or source material "
        "reference has already been archived. A node like this is currently degraded — the "
        "archived reference silently drops out of the bot's prompt instead of raising an error. "
        "This command only reports; it does not modify any data."
    )

    def handle(self, *args, **options):
        degraded_nodes = (
            Node.objects.filter(is_archived=False)
            .filter(
                Q(assistant__is_archived=True)
                | Q(source_material__is_archived=True)
                | Q(collection__is_archived=True)
                | Q(collection_indexes__is_archived=True)
            )
            .distinct()
            .select_related("pipeline", "pipeline__team")
        )

        if not degraded_nodes.exists():
            self.stdout.write(self.style.SUCCESS("No live nodes reference an already-archived resource."))
            return

        self.stdout.write(
            self.style.WARNING(f"Found {degraded_nodes.count()} live node(s) with an archived reference:")
        )
        for node in degraded_nodes:
            archived_fields = [field for field in _SCALAR_FK_FIELDS if self._is_archived(node, field)]
            if node.collection_indexes.filter(is_archived=True).exists():
                archived_fields.append("collection_indexes")

            self.stdout.write(
                f"  team={node.pipeline.team.slug} pipeline={node.pipeline.name!r} "
                f"node={node.flow_id} fields={archived_fields}"
            )

    @staticmethod
    def _is_archived(node, field_name: str) -> bool:
        related = getattr(node, field_name, None)
        return related is not None and related.is_archived
