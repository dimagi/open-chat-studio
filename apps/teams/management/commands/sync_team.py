"""Pull a team's data from a source Open Chat Studio server and recreate it locally.

    manage.py sync_team --source-url=<src> --api-key=<key> --team-slug=<slug> [--private-key-path=<path>]

The command is a thin shell: it wires the source client to the import engine and the local FK
translation store. Each run makes one pass over the manifest and exits; rerun to pick up new data.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from field_audit.models import AuditAction

from apps.teams.models import Team
from apps.teams.sync.client import SourceClient
from apps.teams.sync.emails import send_password_reset_email
from apps.teams.sync.importer import Importer
from apps.teams.sync.manifest import entry_model, schema_checksum
from apps.teams.sync.seal import load_private_key
from apps.teams.sync.translation import (
    FKTranslationStore,
    derive_pk_cursor,
    derive_updated_at_cursor,
)


def _start_cursor(model_label, cursor_type, store, model):
    """Resume each model from the rows already synced (no cursor is persisted separately)."""
    committed = store.committed_targets(model_label)
    if not committed:
        return None
    if cursor_type == "pk":
        return derive_pk_cursor(committed.keys())
    source_by_target = {target: source for source, target in committed.items()}
    pairs = [
        (updated_at, source_by_target[pk])
        for pk, updated_at in model.objects.filter(pk__in=source_by_target).values_list("pk", "updated_at")
    ]
    return derive_updated_at_cursor(pairs)


def run_sync(
    client,
    store,
    private_key,
    write=lambda _m: None,
    page_limit=100,
    enforce_schema=True,
    on_user_created=send_password_reset_email,
):
    manifest = client.get_manifest()
    if enforce_schema and manifest.get("schema_checksum") != schema_checksum():
        raise CommandError(
            "Source and target schema checksums differ; bring both to the same migration state "
            "before syncing, or pass --skip-schema-check to override."
        )

    importer = Importer(store, private_key=private_key, on_user_created=on_user_created)
    for entry in manifest["entries"]:
        model_label, resource, cursor_type = entry["model"], entry["resource"], entry["cursor"]
        model = entry_model(model_label)
        cursor = _start_cursor(model_label, cursor_type, store, model)
        importer.import_rows(model_label, client.iter_rows(resource, start_cursor=cursor, limit=page_limit))
        write(f"synced {resource}")
    return importer


def force_delete_team(team_slug, state_dir, write=lambda _m: None):
    """Delete the local team (matched by slug) and its sync-state DB so the next run re-imports from
    scratch. Without resetting the state, the derived cursor would skip the rows that were deleted."""
    deleted, _ = Team.objects.filter(slug=team_slug).delete(audit_action=AuditAction.AUDIT)
    state_db = Path(state_dir) / f"{team_slug}.sqlite"
    state_db.unlink(missing_ok=True)
    write(f"force-deleted team '{team_slug}' and reset sync state ({deleted} objects removed)")


class Command(BaseCommand):
    help = "Sync a team's data from a source OCS server into this one."

    def add_arguments(self, parser):
        parser.add_argument("--source-url", required=True)
        parser.add_argument("--api-key", required=True)
        parser.add_argument(
            "--team-slug",
            required=True,
            help="Names the local run state DB and identifies the team to drop when --force-delete is set.",
        )
        parser.add_argument("--private-key-path", help="RSA private key used to unseal secret fields.")
        parser.add_argument("--state-dir", default=".", help="Directory for the per-team SQLite state DB.")
        parser.add_argument("--limit", type=int, default=100, help="Page size for resource requests.")
        parser.add_argument(
            "--skip-schema-check",
            action="store_true",
            help="Sync even if the source and target schema checksums differ.",
        )
        parser.add_argument(
            "--force-delete",
            action="store_true",
            help="Delete the local team and its sync state before syncing, for a clean re-import.",
        )

    def handle(self, *args, **options):
        if options["force_delete"]:
            force_delete_team(
                options["team_slug"],
                options["state_dir"],
                write=lambda message: self.stdout.write(self.style.WARNING(message)),
            )

        private_key = None
        if options["private_key_path"]:
            private_key = load_private_key(Path(options["private_key_path"]).read_bytes())

        store = FKTranslationStore(Path(options["state_dir"]) / f"{options['team_slug']}.sqlite")
        client = SourceClient(options["source_url"], options["api_key"])

        run_sync(
            client,
            store,
            private_key,
            write=self.stdout.write,
            page_limit=options["limit"],
            enforce_schema=not options["skip_schema_check"],
        )

        if store.has_unfilled_targets():
            self.stdout.write(self.style.WARNING("Some rows are not yet synced; rerun to complete."))
        else:
            self.stdout.write(self.style.SUCCESS("Sync complete."))
