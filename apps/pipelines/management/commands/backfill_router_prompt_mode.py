from django.db.models import Q

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.pipelines.models import Node
from apps.pipelines.nodes.nodes import RouterPromptMode

BATCH_SIZE = 1000


class Command(IdempotentCommand):
    help = "Store prompt_mode=custom on LLM Router nodes saved before prompt modes existed."
    migration_name = "backfill_router_prompt_mode_2026_10_30"
    atomic = False  # batches are independent; let each commit on its own

    def perform_migration(self, dry_run=False):
        # get_all() includes archived nodes, so a pipeline version restored later is backfilled too.
        routers = (
            Node.objects.get_all()
            .filter(type="RouterNode")
            .filter(Q(params__prompt_mode__isnull=True) | Q(params__prompt_mode=None) | Q(params__prompt_mode=""))
        )
        if dry_run:
            self.stdout.write(f"Would backfill prompt_mode on {routers.count()} router nodes")
            return None

        updated = 0
        batch = []
        for node in routers.only("id", "params").iterator(chunk_size=BATCH_SIZE):
            node.params["prompt_mode"] = RouterPromptMode.CUSTOM.value
            batch.append(node)
            if len(batch) >= BATCH_SIZE:
                updated += self._save(batch)
                batch = []
        updated += self._save(batch)

        self.stdout.write(self.style.SUCCESS(f"Done. Backfilled {updated} router nodes"))
        return updated

    @staticmethod
    def _save(nodes: list[Node]) -> int:
        Node.objects.get_all().bulk_update(nodes, ["params"])
        return len(nodes)
