import logging
from collections import defaultdict

from django.core.management import CommandError

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.pipelines.models import Node
from apps.teams.models import Team
from apps.utils.fields import as_int

logger = logging.getLogger(__name__)

BATCH_SIZE = 2000


class Command(IdempotentCommand):
    help = "Backfill resource FK fields on Node from params JSON."
    migration_name = "backfill_node_resource_fks_2026_06_17"
    atomic = False  # batches are independent; let each commit on its own

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--team", help="Slug of the team to backfill (default: all teams)", required=False)

    def handle(self, *args, **options):
        team_slug = options.get("team")
        if team_slug:
            try:
                self.team = Team.objects.get(slug=team_slug)
            except Team.DoesNotExist:
                raise CommandError(f"Team '{team_slug}' does not exist.") from None
        else:
            self.team = None
        super().handle(*args, **options)

    def _base_qs(self):
        qs = Node.objects.get_all()
        if self.team:
            qs = qs.filter(pipeline__team=self.team)
        return qs

    def _load_valid_fk_ids(self, fk_fields):
        """Pre-fetch all valid IDs for each resource FK to guard against dangling references."""
        valid = {}
        for name in fk_fields:
            field = Node._meta.get_field(name)
            ids = set(field.related_model.objects.values_list("id", flat=True))
            valid[f"{name}_id"] = ids
            self.stdout.write(f"  Loaded {len(ids)} valid {name} IDs")
        return valid

    def perform_migration(self, dry_run=False):
        # get_all() bypasses the default manager's is_archived=False filter so the mirror is
        # backfilled on every node — working versions, published versions, and soft-deleted
        # (archived) versions alike. Archived versions still hold references that the mirror must reflect.
        node_ids = list(self._base_qs().values_list("id", flat=True))
        total = len(node_ids)

        scope = f"team '{self.team.slug}'" if self.team else "all teams"
        if dry_run:
            self.stdout.write(f"Would backfill FK fields for {total} nodes ({scope})")
            return

        self.stdout.write(f"Backfilling FK fields for {total} nodes ({scope})...")

        fk_fields = Node.resource_fk_fields()
        fk_id_attrs = [f"{name}_id" for name in fk_fields]
        valid_fk_ids = self._load_valid_fk_ids(fk_fields)
        processed = 0

        for start in range(0, total, BATCH_SIZE):
            chunk_ids = node_ids[start : start + BATCH_SIZE]
            nodes = list(self._base_qs().filter(id__in=chunk_ids).only("id", "params", *fk_id_attrs))
            self._backfill_scalar_fks(nodes, fk_id_attrs, valid_fk_ids)
            self._backfill_collection_indexes(nodes, valid_fk_ids["collection_id"])
            processed += len(nodes)
            self.stdout.write(f"  ...{processed}/{total}")

        self.stdout.write(self.style.SUCCESS(f"Done. Processed: {processed}"))
        return processed

    def _backfill_scalar_fks(self, nodes, fk_id_attrs, valid_fk_ids):
        """Mirror the scalar FK columns from params, bulk-updating only the nodes that changed.

        IDs that don't exist in valid_fk_ids are treated as None to avoid integrity errors.
        """
        changed = []
        for node in nodes:
            params = node.params or {}
            node_changed = False
            for attr in fk_id_attrs:
                value = as_int(params.get(attr))
                if value is not None and value not in valid_fk_ids[attr]:
                    value = None
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

    def _backfill_collection_indexes(self, nodes, valid_collection_ids):
        """Reconcile the collection_indexes M2M through table to mirror collection_index_ids in params.

        Mirrors Node._sync_resource_fk_fields: ids are coerced via as_int (malformed values dropped)
        and intersected with the preloaded valid_collection_ids set (non-existent ones are dropped).
        """
        through = Node.collection_indexes.through
        node_ids = [node.id for node in nodes]

        desired_by_node = {}
        for node in nodes:
            raw_ids = (node.params or {}).get("collection_index_ids") or []
            if not isinstance(raw_ids, list | tuple | set):
                raw_ids = [raw_ids]
            ids = {parsed for parsed in map(as_int, raw_ids) if parsed is not None}
            desired_by_node[node.id] = ids

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
            desired = desired_by_node[node.id] & valid_collection_ids
            current = existing_by_node.get(node.id, set())
            to_create.extend(through(node_id=node.id, collection_id=cid) for cid in desired - current)
            stale_row_ids.extend(row_pk[(node.id, cid)] for cid in current - desired)

        if to_create:
            through.objects.bulk_create(to_create, batch_size=BATCH_SIZE, ignore_conflicts=True)
        if stale_row_ids:
            through.objects.filter(id__in=stale_row_ids).delete()
        self.stdout.write(f"    collection indexes: {len(to_create)} created, {len(stale_row_ids)} deleted")
