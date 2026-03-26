from django.core.management.base import BaseCommand, CommandError

from apps.documents.models import Collection, CollectionFile


class Command(BaseCommand):
    help = "Populate unsupported_channels for files in media (non-indexed) collections."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--collection-id", type=int, help="Process a single collection by ID")
        group.add_argument("--team", type=str, help="Process all media collections for a team slug")
        parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")

    def handle(self, *args, **options):
        collection_id = options["collection_id"]
        team_slug = options["team"]
        dry_run = options["dry_run"]

        if not collection_id and not team_slug:
            raise CommandError("You must provide either --collection-id or --team.")

        if collection_id:
            try:
                Collection.objects.get(id=collection_id)
            except Collection.DoesNotExist as err:
                raise CommandError(f"Collection with ID {collection_id} does not exist.") from err
            collections = Collection.objects.filter(id=collection_id, is_index=False)
        else:
            collections = Collection.objects.filter(team__slug=team_slug, is_index=False)

        files_to_process = list(CollectionFile.objects.filter(collection__in=collections).select_related("file"))

        for cf in files_to_process:
            cf.update_supported_channels()

        total = len(files_to_process)
        unsendable_count = sum(1 for cf in files_to_process if cf.unsupported_channels)

        if dry_run:
            self.stdout.write(
                f"Dry run: {total} files processed, {unsendable_count} with unsupported channels. No changes written."
            )
            return

        CollectionFile.objects.bulk_update(files_to_process, ["unsupported_channels"])

        self.stdout.write(self.style.SUCCESS(f"{total} files processed, {unsendable_count} with unsupported channels."))
