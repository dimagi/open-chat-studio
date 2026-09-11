from django.db.models import Q

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.files.models import File, FilePurpose
from apps.utils.deletion import get_related_m2m_objects

ASSISTANT_PURPOSE = "assistant"

# Where a file that once belonged to an assistant now really lives, in precedence order,
# matching backfill_file_purpose's RULES. The create-from-assistant flow linked existing files
# into collections without repurposing them. The document_sources arm is subsumed by collections
# (both run through CollectionFile, whose collection FK is non-null) and is kept only so this
# list reads the same as the rules it mirrors.
REPOINT_RULES: list[tuple[str, Q]] = [
    (FilePurpose.COLLECTION, Q(collections__isnull=False) | Q(document_sources__isnull=False)),
    (FilePurpose.MESSAGE_MEDIA, Q(chatattachment__isnull=False)),
]

# Deletable only with no live reference of any kind. Mirrors cleanup_orphaned_files: reverse FKs
# must be named explicitly because the generic m2m re-check below covers only m2m relations.
UNREFERENCED = Q(
    collections__isnull=True,
    document_sources__isnull=True,
    chatattachment__isnull=True,
    syntheticvoice__isnull=True,
    filechunkembedding__isnull=True,
    working_version__isnull=True,
    versions__isnull=True,
)

DELETE_BATCH_SIZE = 500


class Command(IdempotentCommand):
    help = "Repoint or delete File rows left on the retired 'assistant' purpose"
    migration_name = "retire_assistant_file_purpose_2026_10_07"

    def perform_migration(self, dry_run=False):
        repointed = self._repoint(dry_run)
        deleted = self._delete_unreferenced(dry_run)
        stranded = File.objects.filter(purpose=ASSISTANT_PURPOSE).count()

        self.stdout.write(f"  repointed: {repointed}")
        self.stdout.write(f"  deleted: {deleted}")
        if stranded:
            self.stdout.write(self.style.WARNING(f"  left alone (referenced by an unclassified relation): {stranded}"))

        verb = "Would repoint" if dry_run else "Repointed"
        return f"{verb} {repointed}, deleted {deleted}, left {stranded}"

    def _repoint(self, dry_run) -> int:
        total = 0
        for purpose, condition in REPOINT_RULES:
            ids = list(
                File.objects.filter(condition, purpose=ASSISTANT_PURPOSE).values_list("id", flat=True).distinct()
            )
            if not ids:
                continue
            total += len(ids)
            if self.verbosity > 1:
                self.stdout.write(f"  {purpose}: {len(ids)}")
            if not dry_run:
                File.objects.filter(id__in=ids).update(purpose=purpose)
        return total

    def _delete_unreferenced(self, dry_run) -> int:
        candidates = list(File.objects.filter(UNREFERENCED, purpose=ASSISTANT_PURPOSE).select_related("team"))

        # Belt-and-braces: UNREFERENCED only knows the relations it names, so re-check every
        # candidate generically and drop anything referenced by one it missed.
        referenced = get_related_m2m_objects(candidates)
        orphans = [file for file in candidates if file not in referenced]

        if self.verbosity > 1 and (skipped := len(candidates) - len(orphans)):
            self.stdout.write(f"  skipped (referenced via other relation): {skipped}")

        if not dry_run and orphans:
            ids = [file.pk for file in orphans]
            # Per-instance signals fire on queryset delete, so django_cleanup removes each
            # underlying storage object as its row is deleted.
            for start in range(0, len(ids), DELETE_BATCH_SIZE):
                File.objects.filter(pk__in=ids[start : start + DELETE_BATCH_SIZE]).delete()

        return len(orphans)
