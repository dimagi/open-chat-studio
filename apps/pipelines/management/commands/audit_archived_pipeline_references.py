from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.pipelines.models import Node
from apps.pipelines.versioning import all_versioned_param_specs

# The list-valued spec (`collection_index_ids`, mirrored onto the `collection_indexes` M2M) is
# handled separately via its own `.filter(is_archived=True).exists()` check below.
_SCALAR_FK_FIELDS = tuple(dict.fromkeys(spec.fk_field for spec in all_versioned_param_specs() if not spec.many))


class Command(BaseCommand):
    help = (
        "Read-only audit: list live pipeline nodes whose assistant, collection, or source material "
        "reference has already been archived. A node like this is currently degraded — the "
        "archived reference silently drops out of the bot's prompt instead of raising an error. "
        "This command only reports; it does not modify any data."
    )

    def handle(self, *args, **options):
        degraded_nodes = list(
            Node.objects.filter(
                Q(assistant__is_archived=True)
                | Q(source_material__is_archived=True)
                | Q(collection__is_archived=True)
                | Q(collection_indexes__is_archived=True)
            )
            .distinct()
            .select_related("pipeline", "pipeline__team", *_SCALAR_FK_FIELDS)
            .prefetch_related("collection_indexes")
        )

        if not degraded_nodes:
            self.stdout.write(self.style.SUCCESS("No live nodes reference an already-archived resource."))
            return

        self.stdout.write(self.style.WARNING(f"Found {len(degraded_nodes)} live node(s) with an archived reference:"))
        for node in degraded_nodes:
            archived_fields = [
                self._describe(node, field) for field in _SCALAR_FK_FIELDS if self._is_archived(node, field)
            ]
            archived_indexes = [index for index in node.collection_indexes.all() if index.is_archived]
            if archived_indexes:
                index_descriptions = ", ".join(self._describe_instance(index) for index in archived_indexes)
                archived_fields.append(f"collection_indexes=[{index_descriptions}]")

            self.stdout.write(
                f"  team={node.pipeline.team.slug} pipeline={node.pipeline.name!r} "
                f"node_id={node.id} node={node.flow_id} fields=[{', '.join(archived_fields)}]"
            )

    @staticmethod
    def _is_archived(node, field_name: str) -> bool:
        related = getattr(node, field_name, None)
        return related is not None and related.is_archived

    @classmethod
    def _describe(cls, node, field_name: str) -> str:
        related = getattr(node, field_name)
        return f"{field_name}={cls._describe_instance(related)}"

    @staticmethod
    def _describe_instance(instance) -> str:
        name = getattr(instance, "name", None) or getattr(instance, "topic", None)
        return f"{instance.id}:{name!r}" if name else str(instance.id)
