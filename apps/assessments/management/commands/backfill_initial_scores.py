from apps.data_migrations.management.commands.base import IdempotentCommand


class Command(IdempotentCommand):
    help = "Backfill Score rows from existing EvaluationResults and submitted Annotations"
    migration_name = "backfill_initial_scores_2026_05_19"
    atomic = False  # per-row work is independent; let each commit on its own

    def perform_migration(self, dry_run=False):
        # Local imports keep import-time light; this command is rarely invoked.
        from apps.assessments.score_writers import (  # noqa: PLC0415
            write_scores_from_annotation,
            write_scores_from_evaluation_result,
        )
        from apps.evaluations.models import EvaluationResult  # noqa: PLC0415
        from apps.human_annotations.models import Annotation, AnnotationStatus  # noqa: PLC0415

        eval_qs = EvaluationResult.objects.select_related(
            "message__session",
            "evaluator",
            "team",
        )
        ann_qs = Annotation.objects.filter(status=AnnotationStatus.SUBMITTED).select_related(
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
