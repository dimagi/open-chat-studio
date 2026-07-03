from collections import defaultdict

from django.db.models import Q

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.files.models import File
from apps.utils.deletion import get_related_m2m_objects

# Export name patterns (see backfill_file_purpose). Excluded defensively so an
# export that has not yet been classified/expired is never treated as an orphan.
EXPORT_NAME_PATTERNS = (
    Q(name__icontains="Chat Export") | Q(name__icontains="_latest_results_") | Q(name__icontains="_files_")
)

# A file is an orphan-cleanup candidate only if it has no live reference of any
# kind: no collection/document-source membership, no assistant tool resource, no
# chat attachment, no voice sample FK, no external (OpenAI) source, and no version
# relationship (a working copy or a version of one is retained for history, not
# deleted). purpose="" restricts this to the legacy rows the backfill left behind.
UNREFERENCED = Q(
    collections__isnull=True,
    document_sources__isnull=True,
    toolresources__isnull=True,
    chatattachment__isnull=True,
    syntheticvoice__isnull=True,
    working_version__isnull=True,
    versions__isnull=True,
)

DELETE_BATCH_SIZE = 500


class Command(IdempotentCommand):
    help = "Delete legacy orphaned files (purpose unset, no live reference) and their storage objects"
    migration_name = "cleanup_orphaned_files_2026_07_03"

    def perform_migration(self, dry_run=False):
        candidates = self._candidates()

        # Belt-and-braces: the ORM filter above only knows the relations we named,
        # so re-check every candidate against all m2m relations generically and
        # drop anything that turns out to be referenced by a relation we missed.
        referenced = get_related_m2m_objects(candidates)
        orphans = [f for f in candidates if f not in referenced]
        skipped = len(candidates) - len(orphans)

        by_team: dict[str, int] = defaultdict(int)
        total_bytes = 0
        for f in orphans:
            by_team[f.team.slug] += 1
            total_bytes += f.content_size or 0

        for slug in sorted(by_team):
            self.stdout.write(f"  {slug}: {by_team[slug]}")
        self.stdout.write(f"  orphans to delete: {len(orphans)} ({total_bytes / 1_000_000:.1f} MB)")
        if skipped:
            self.stdout.write(f"  skipped (referenced via other relation): {skipped}")

        if not dry_run and orphans:
            self._delete([f.pk for f in orphans])

        return {"deleted": 0 if dry_run else len(orphans)}

    @staticmethod
    def _candidates() -> list[File]:
        return list(
            File.objects.filter(UNREFERENCED, purpose="")
            .exclude(external_source="openai")
            .exclude(EXPORT_NAME_PATTERNS)
            .select_related("team")
        )

    @staticmethod
    def _delete(ids: list[int]) -> None:
        # Per-instance signals fire on queryset delete, so django_cleanup removes
        # each underlying storage object as its row is deleted.
        for start in range(0, len(ids), DELETE_BATCH_SIZE):
            File.objects.filter(pk__in=ids[start : start + DELETE_BATCH_SIZE]).delete()
