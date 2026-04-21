import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.custom_actions.models import CustomAction
from apps.pipelines.models import Node, Pipeline
from apps.pipelines.nodes.nodes import LLMResponseWithPrompt

log = logging.getLogger(__name__)

BATCH_SIZE = 500
CUSTOM_ACTIONS_FIELD = "custom_actions"


class Command(BaseCommand):
    help = (
        "Strip references to deleted CustomActions from pipeline Node.params['custom_actions'] "
        "and Pipeline.data['nodes'][*]['data']['params']['custom_actions']. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing to the database.",
        )

    def handle(self, *args, dry_run: bool = False, verbosity: int = 1, **options):
        self.verbosity = verbosity
        self.existing_ids = set(CustomAction.objects.values_list("id", flat=True))

        with transaction.atomic():
            nodes_updated, affected_pipeline_ids = self._scrub_nodes(dry_run)
            pipelines_updated = self._scrub_pipelines(dry_run, affected_pipeline_ids)

            if dry_run:
                transaction.set_rollback(True)

        verb = "would update" if dry_run else "updated"
        summary = f"{verb} {nodes_updated} node(s) and {pipelines_updated} pipeline(s)."
        log.info("cleanup_stale_custom_action_refs: %s (dry_run=%s)", summary, dry_run)
        self.stdout.write(self.style.SUCCESS(summary))

    def _is_stale(self, ref) -> bool:
        if not isinstance(ref, str):
            log.warning("cleanup_stale_custom_action_refs: non-string ref skipped: %r", ref)
            return False
        head, _, _ = ref.partition(":")
        try:
            return int(head) not in self.existing_ids
        except ValueError:
            log.warning("cleanup_stale_custom_action_refs: unparseable ref skipped: %r", ref)
            return False

    def _partition_refs(self, refs):
        cleaned, dropped = [], []
        for ref in refs:
            (dropped if self._is_stale(ref) else cleaned).append(ref)
        return cleaned, dropped

    @staticmethod
    def _llm_nodes_with_custom_actions():
        # Skip empty lists and nulls at the DB level so unrelated rows never leave Postgres.
        return (
            Node.objects.filter(type=LLMResponseWithPrompt.__name__)
            .exclude(params__custom_actions__isnull=True)
            .exclude(params__custom_actions=[])
        )

    def _scrub_nodes(self, dry_run):
        qs = self._llm_nodes_with_custom_actions().only("id", "pipeline_id", "flow_id", "params")

        updated = 0
        pending = []
        affected_pipeline_ids = set()
        for node in qs.iterator(chunk_size=BATCH_SIZE):
            refs = node.params.get(CUSTOM_ACTIONS_FIELD) or []
            if not isinstance(refs, list):
                continue
            cleaned, dropped = self._partition_refs(refs)
            if not dropped:
                continue
            msg = f"Node id={node.id} pipeline={node.pipeline_id} flow_id={node.flow_id}: dropping {dropped}"
            log.info("cleanup_stale_custom_action_refs: %s", msg)
            if self.verbosity >= 2:
                self.stdout.write(msg)
            node.params[CUSTOM_ACTIONS_FIELD] = cleaned
            affected_pipeline_ids.add(node.pipeline_id)
            updated += 1
            pending.append(node)
            if not dry_run and len(pending) >= BATCH_SIZE:
                Node.objects.bulk_update(pending, ["params"])
                pending.clear()

        if not dry_run and pending:
            Node.objects.bulk_update(pending, ["params"])

        return updated, affected_pipeline_ids

    def _scrub_pipelines(self, dry_run, seed_pipeline_ids):
        # Union of pipelines we just cleaned + pipelines still referencing any CustomAction,
        # to catch drift where Pipeline.data holds a ref that its child Node no longer does.
        referencing_pipeline_ids = set(self._llm_nodes_with_custom_actions().values_list("pipeline_id", flat=True))
        candidate_ids = seed_pipeline_ids | referencing_pipeline_ids
        if not candidate_ids:
            return 0

        qs = Pipeline.objects.filter(id__in=candidate_ids).only("id", "data")

        to_update = []
        updated = 0
        for pipeline in qs.iterator(chunk_size=BATCH_SIZE):
            if not self._scrub_pipeline_data(pipeline):
                continue
            updated += 1
            to_update.append(pipeline)
            if not dry_run and len(to_update) >= BATCH_SIZE:
                Pipeline.objects.bulk_update(to_update, ["data"])
                to_update.clear()

        if not dry_run and to_update:
            Pipeline.objects.bulk_update(to_update, ["data"])

        return updated

    def _scrub_pipeline_data(self, pipeline) -> bool:
        changed = False
        for flow_node in (pipeline.data or {}).get("nodes", []) or []:
            params = (flow_node.get("data") or {}).get("params") or {}
            refs = params.get(CUSTOM_ACTIONS_FIELD)
            if not isinstance(refs, list) or not refs:
                continue
            cleaned, dropped = self._partition_refs(refs)
            if not dropped:
                continue
            msg = f"Pipeline id={pipeline.id} flow_id={flow_node.get('id')}: dropping {dropped}"
            log.info("cleanup_stale_custom_action_refs: %s", msg)
            if self.verbosity >= 2:
                self.stdout.write(msg)
            params[CUSTOM_ACTIONS_FIELD] = cleaned
            changed = True
        return changed
