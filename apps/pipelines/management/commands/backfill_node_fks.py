import logging
from collections import defaultdict

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.documents.models import Collection
from apps.pipelines.models import Node
from apps.utils.fields import as_int

logger = logging.getLogger(__name__)

BATCH_SIZE = 2000


class Command(IdempotentCommand):
    help = "Backfill resource FK fields on Node from params JSON."
    migration_name = "backfill_node_resource_fks_2026_06_17"
    atomic = False  # batches are independent; let each commit on its own

    def perform_migration(self, dry_run=False):
        # get_all() bypasses the default manager's is_archived=False filter so the mirror is
        # backfilled on every node — working versions, published versions, and soft-deleted
        # (archived) versions alike. Archived versions still hold references that the mirror must reflect.
        node_ids = list(Node.objects.get_all().values_list("id", flat=True))
        total = len(node_ids)

        if dry_run:
            self.stdout.write(f"Would backfill FK fields for {total} nodes")
            return

        self.stdout.write(f"Backfilling FK fields for {total} nodes...")

        fk_fields = Node.resource_fk_fields()
        fk_id_attrs = [f"{name}_id" for name in fk_fields]
        processed = 0

        for start in range(0, total, BATCH_SIZE):
            chunk_ids = node_ids[start : start + BATCH_SIZE]
            nodes = list(Node.objects.get_all().filter(id__in=chunk_ids).only("id", "params", *fk_id_attrs))
            self._backfill_scalar_fks(nodes, fk_id_attrs)
            self._backfill_collection_indexes(nodes)
            processed += len(nodes)
            self.stdout.write(f"  ...{processed}/{total}")

        self.stdout.write(self.style.SUCCESS(f"Done. Processed: {processed}"))
        return processed

    def _backfill_scalar_fks(self, nodes, fk_id_attrs):
        """Mirror the scalar FK columns from params, bulk-updating only the nodes that changed."""
        changed = []
        for node in nodes:
            params = node.params or {}
            node_changed = False
            for attr in fk_id_attrs:
                value = as_int(params.get(attr))
                if getattr(node, attr) != value:
                    setattr(node, attr, value)
                    node_changed = True
            if node_changed:
                changed.append(node)
        if changed:
            # get_all() so bulk_update's queryset isn't filtered to is_archived=False — archived
            # node versions must be mirrored too (see perform_migration).
            Node.objects.get_all().bulk_update(changed, fk_id_attrs, batch_size=BATCH_SIZE)
        self.stdout.write(f"    scalar FKs: {len(changed)}/{len(nodes)} nodes updated")

    def _backfill_collection_indexes(self, nodes):
        """Reconcile the collection_indexes M2M through table to mirror collection_index_ids in params.

        Mirrors Node._sync_resource_fk_fields: ids are coerced via as_int (malformed values dropped)
        and filtered to Collections that still exist via the default manager (archived ones are dropped).
        """
        through = Node.collection_indexes.through
        node_ids = [node.id for node in nodes]

        desired_by_node = {}
        referenced_ids = set()
        for node in nodes:
            raw_ids = (node.params or {}).get("collection_index_ids") or []
            if not isinstance(raw_ids, list | tuple | set):
                raw_ids = [raw_ids]
            ids = {parsed for parsed in map(as_int, raw_ids) if parsed is not None}
            desired_by_node[node.id] = ids
            referenced_ids |= ids

        valid_ids = (
            set(Collection.objects.filter(id__in=referenced_ids).values_list("id", flat=True))
            if referenced_ids
            else set()
        )

        existing_by_node = defaultdict(set)
        row_pk = {}
        for row_id, node_id, collection_id in through.objects.filter(node_id__in=node_ids).values_list(
            "id", "node_id", "collection_id"
        ):
            existing_by_node[node_id].add(collection_id)
            row_pk[(node_id, collection_id)] = row_id

        to_create = []
        stale_row_ids = []
        for node in nodes:
            desired = desired_by_node[node.id] & valid_ids
            current = existing_by_node.get(node.id, set())
            to_create.extend(through(node_id=node.id, collection_id=cid) for cid in desired - current)
            stale_row_ids.extend(row_pk[(node.id, cid)] for cid in current - desired)

        if to_create:
            through.objects.bulk_create(to_create, batch_size=BATCH_SIZE, ignore_conflicts=True)
        if stale_row_ids:
            through.objects.filter(id__in=stale_row_ids).delete()
        self.stdout.write(f"    collection indexes: {len(to_create)} created, {len(stale_row_ids)} deleted")
