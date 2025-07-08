import sys

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.documents.document_source_service import sync_document_source
from apps.documents.models import DocumentSource, SourceType


class Command(BaseCommand):
    help = "Sync document sources with their external sources"

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-type",
            choices=[choice[0] for choice in SourceType.choices],
            help="Only sync sources of this type",
        )
        parser.add_argument(
            "--collection-id",
            type=int,
            help="Only sync the source for this collection ID",
        )
        parser.add_argument(
            "--auto-only",
            action="store_true",
            help="Only sync sources with auto_sync_enabled=True",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be synced without actually syncing",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force sync even if recently synced",
        )

    def handle(self, *args, **options):
        # Build queryset based on options
        queryset = DocumentSource.objects.select_related("collection", "collection__team")

        if options["source_type"]:
            queryset = queryset.filter(source_type=options["source_type"])

        if options["collection_id"]:
            queryset = queryset.filter(collection_id=options["collection_id"])

        if options["auto_only"]:
            queryset = queryset.filter(auto_sync_enabled=True)

        # Only sync indexed collections
        queryset = queryset.filter(collection__is_index=True)

        if not queryset.exists():
            self.stdout.write(self.style.WARNING("No document sources found matching the criteria."))
            return

        if options["dry_run"]:
            self.stdout.write(self.style.NOTICE(f"Would sync {queryset.count()} document sources:"))
            for source in queryset:
                self.stdout.write(f"  - {source} ({source.get_source_type_display()})")
            return

        # Perform the sync
        total_synced = 0
        total_failed = 0

        for source in queryset:
            try:
                self.stdout.write(f"Syncing {source}...", ending=" ")

                with transaction.atomic():
                    result = sync_document_source(source)

                if result.success:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"✓ ({result.files_added} added, {result.files_updated} updated, "
                            f"{result.files_removed} removed)"
                        )
                    )
                    total_synced += 1
                else:
                    self.stdout.write(self.style.ERROR(f"✗ {result.error_message}"))
                    total_failed += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✗ Unexpected error: {str(e)}"))
                total_failed += 1

                if options["verbosity"] >= 2:
                    import traceback

                    self.stderr.write(traceback.format_exc())

        # Summary
        self.stdout.write("")
        if total_synced > 0:
            self.stdout.write(self.style.SUCCESS(f"Successfully synced {total_synced} document sources."))

        if total_failed > 0:
            self.stdout.write(self.style.ERROR(f"Failed to sync {total_failed} document sources."))
            sys.exit(1)

        if total_synced == 0 and total_failed == 0:
            self.stdout.write(self.style.WARNING("No document sources were synced."))
