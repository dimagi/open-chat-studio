from apps.assessments.score_writers import (
    write_scores_from_annotation,
    write_scores_from_evaluation_result,
)
from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.evaluations.models import EvaluationResult
from apps.human_annotations.models import Annotation, AnnotationStatus


class Command(IdempotentCommand):
    help = "Backfill Score rows from existing EvaluationResults and submitted Annotations"
    migration_name = "backfill_initial_scores_2026_05_19"
    atomic = False  # per-row work is independent; let each commit on its own

    def perform_migration(self, dry_run=False):
        # Both writers no-op when the source row has no session; pre-filter at the
        # queryset so we don't iterate (and log/count) work we'd just throw away.
        eval_qs = EvaluationResult.objects.filter(message__session__isnull=False).select_related(
            "message__session",
            "evaluator",
            "team",
        )
        ann_qs = Annotation.objects.filter(
            status=AnnotationStatus.SUBMITTED,
            item__session__isnull=False,
        ).select_related(
            "item__queue",
            "item__session",
            "team",
            "reviewer",
        )

        if dry_run:
            self.stdout.write(f"Would write Scores for {eval_qs.count()} eval results, {ann_qs.count()} annotations")
            return

        written = 0
        failed = 0
        for result in eval_qs.iterator(chunk_size=500):
            try:
                write_scores_from_evaluation_result(result)
                written += 1
            except Exception as exc:
                failed += 1
                self.stderr.write(f"Failed eval result {result.id}: {exc}")
        for annotation in ann_qs.iterator(chunk_size=500):
            try:
                write_scores_from_annotation(annotation)
                written += 1
            except Exception as exc:
                failed += 1
                self.stderr.write(f"Failed annotation {annotation.id}: {exc}")
        self.stdout.write(f"Backfill processed {written} source rows (failed: {failed})")
        return written
