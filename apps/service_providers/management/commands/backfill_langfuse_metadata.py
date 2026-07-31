"""Populate `TraceProvider.metadata` with Langfuse project/organization details.

New and edited providers get this on save; this command covers the ones that
predate that. Safe to re-run: providers that already carry a project id are
skipped unless `--force` is given, so a run that hit a few API failures can
simply be repeated.
"""

from django.core.management.base import BaseCommand

from apps.service_providers.models import TraceProvider, TraceProviderType
from apps.service_providers.tracing.langfuse import fetch_project_metadata


class Command(BaseCommand):
    help = "Fetch Langfuse project/organization details for existing Langfuse trace providers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-fetch providers that already have project details recorded.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and report, but don't write anything.",
        )
        parser.add_argument(
            "--provider-id",
            type=int,
            action="append",
            dest="provider_ids",
            help="Limit to these provider ids (repeatable).",
        )
        parser.add_argument(
            "--team",
            type=str,
            dest="team_slug",
            help="Limit to providers belonging to this team slug.",
        )

    def handle(self, *args, **options):
        providers = self._get_providers(options)
        if not providers:
            self.stdout.write(self.style.WARNING("No Langfuse trace providers to process."))
            return

        updated, skipped, failed = 0, 0, 0
        for provider in providers:
            label = f"provider #{provider.id} [{provider.team.slug}] {provider.name}"
            if provider.metadata.get("project_id") and not options["force"]:
                self.stdout.write(f"  skip    {label} — already has project details (use --force to re-fetch)")
                skipped += 1
                continue

            try:
                metadata = fetch_project_metadata(provider.config)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"  failed  {label} — {exc}"))
                failed += 1
                continue

            if not options["dry_run"]:
                provider.metadata = metadata
                provider.save(update_fields=["metadata"])
            updated += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ok      {label} — org '{metadata['organization_name']}' / "
                    f"project '{metadata['project_name']}' ({metadata['project_id']})"
                )
            )

        prefix = "Would update" if options["dry_run"] else "Updated"
        self.stdout.write(f"\n{prefix}: {updated}, skipped: {skipped}, failed: {failed}")

    def _get_providers(self, options) -> list[TraceProvider]:
        queryset = TraceProvider.objects.filter(type=TraceProviderType.langfuse).select_related("team")
        if options["provider_ids"]:
            queryset = queryset.filter(id__in=options["provider_ids"])
        if options["team_slug"]:
            queryset = queryset.filter(team__slug=options["team_slug"])
        return list(queryset)
