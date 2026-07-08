"""Pull a team's data from a source Open Chat Studio server and recreate it locally.

    manage.py sync_team --source-url=<src> --api-key=<key> --team-slug=<slug> [--private-key-path=<path>]

The command is a thin shell: it wires the resource fetcher to the import engine and the local FK
translation store. Each run makes one pass over the manifest and exits; rerun to pick up new data.
"""

import time
from datetime import timedelta
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError

from apps.teams.export.client import ResourceFetcher
from apps.teams.export.emails import send_password_reset_email
from apps.teams.export.importer import Importer, mute_signals
from apps.teams.export.manifest import TEAM_MODEL, entry_model, schema_checksum
from apps.teams.export.seal import MISSING_PUBLIC_KEY_DETAIL, load_private_key
from apps.teams.export.translation import (
    FKTranslationStore,
    derive_pk_cursor,
    derive_updated_at_cursor,
)
from apps.teams.models import Team
from apps.teams.utils import current_team
from apps.utils.deletion import delete_object_with_auditing_of_related_objects

FILES_CONFIRMED_FLAG = "files_confirmed"
MIGRATION_MODE_REQUIRED = "Migration mode needs to be enabled on the source team before you can continue."
MISSING_PUBLIC_KEY_MESSAGE = (
    "The source team has no public key registered, so its secret data cannot be exported. "
    "Set the team's public key on the source server before syncing."
)

# Known source-server refusals, matched by status code and detail marker, with the friendly
# message to show the operator instead of a raw HTTP traceback.
_FRIENDLY_HTTP_ERRORS = (
    (
        400,
        MISSING_PUBLIC_KEY_DETAIL,
        MISSING_PUBLIC_KEY_MESSAGE,
    ),
)


def _friendly_http_error_message(exc: requests.HTTPError) -> str | None:
    """The operator-friendly message for a known source refusal, or None when the error is
    unrelated and should surface as-is."""
    response = exc.response
    if response is None:
        return None
    try:
        detail = response.json().get("detail", "")
    except ValueError:
        return None
    for status_code, marker, message in _FRIENDLY_HTTP_ERRORS:
        if response.status_code == status_code and marker in detail:
            return message
    return None


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


def check_source_team_ready(client) -> None:
    """Block the sync unless the source team is in migration mode and has a public key registered --
    both must be set. The export API no longer enforces migration mode server-side, so the client
    checks it here from the team endpoint's ``is_migrating`` / ``has_public_key`` status (the latter is a
    boolean saying whether a key is registered). Raises CommandError listing whatever is missing."""
    team = client.get_team()
    problems = []
    if not team.get("is_migrating"):
        problems.append(MIGRATION_MODE_REQUIRED)
    if not team.get("has_public_key"):
        problems.append(MISSING_PUBLIC_KEY_MESSAGE)
    if problems:
        raise CommandError(" ".join(problems))


def _prompt(message: str) -> str:
    """input() that turns EOF (no terminal -- cron, CI, piped stdin) into a clean abort."""
    try:
        return input(message)
    except EOFError:
        raise CommandError("This command must be run interactively; stdin is closed.") from None


def check_sync_preconditions(client, private_key, enforce_schema=True, store=None) -> dict:
    """Fetch the source manifest and confirm the sync can actually proceed: the source is reachable,
    its schema matches, we hold a key for any sealed secrets, and the source team is ready to export
    (migration mode on, public key set). When a ``store`` is given, also ask the operator to confirm
    the team's files were moved to this server's storage backend -- that happens outside this command
    and the sync fails without it. The answer is recorded in the store only once every check passes,
    so an aborted run asks again while a rerun after a clean preflight doesn't. Returns the manifest.
    Raises CommandError on any failure, before any rows are imported."""

    manifest = client.get_manifest()
    if enforce_schema and manifest.get("schema_checksum") != schema_checksum():
        raise CommandError(
            "Source and target schema checksums differ; bring both to the same migration state "
            "before syncing, or pass --skip-schema-check to override."
        )

    if private_key is None and any(entry.get("secret") for entry in manifest["entries"]):
        raise CommandError(
            "The source exports sealed secret fields but no private key was provided; the secrets "
            "would be imported as unreadable tokens. Pass --private-key-path with the team's key."
        )

    check_source_team_ready(client)

    files_confirmation_needed = store is not None and not store.has_flag(FILES_CONFIRMED_FLAG)
    if files_confirmation_needed:
        answer = _prompt(
            "Have you exported the team's files from the source server and imported them into "
            "this server's storage backend? [yes/no]: "
        )
        if answer.strip().lower() != "yes":
            raise CommandError(
                "The team's files must be exported from the source server and imported into this "
                "server's storage backend before syncing, otherwise the sync will fail. Do that "
                "first, then rerun this command."
            )
    if files_confirmation_needed:
        store.set_flag(FILES_CONFIRMED_FLAG)
    return manifest


def _style_synced_line(message: str, count: int, style) -> str:
    """Colour a progress line by how much it moved: green when rows were synced, muted (ANSI faint)
    when zero so the eye skips the no-op lines. Returns the message unchanged when no style is given
    or colour is disabled (--no-color / non-tty, where the style funcs are the identity), so
    redirected logs stay clean."""
    if style is None:
        return message
    if count > 0:
        return style.SUCCESS(message)
    if style.SUCCESS("") == "":  # colour disabled -- leave it plain rather than emit raw escapes
        return message
    return f"\x1b[2m{message}\x1b[0m"


def _committed_team(store) -> Team | None:
    """The target team a prior run already imported, or None on a first-time import. A recorded
    team that no longer exists locally means the team was deleted outside this sync, so abort with
    the fix rather than a raw DoesNotExist traceback."""
    committed = store.committed_targets(TEAM_MODEL)
    if not committed:
        return None
    team = Team.objects.filter(pk=next(iter(committed.values()))).first()
    if team is None:
        raise CommandError(
            "The sync state references a team that no longer exists locally; the state is stale. "
            "Re-run with --force-delete to reset it and re-import from scratch."
        )
    return team


def load_team(importer, client, store, write):
    """Set the importer's target team, doing the least work needed to anchor the sync.

    Three cases, by where the team is already known:
    - Recorded in the sync store: a prior run imported it, so load it straight from the target DB --
      no source fetch, so the ``team/`` endpoint (and the source migration lock) isn't hit again.
    - Present in the target DB under the same slug but not in the store: a team this sync doesn't track
      (created by hand, or its state DB was lost). Refuse to import over it and tell the operator to
      re-run with --force-delete, rather than silently overwriting a team we didn't create.
    - Neither: a first-time import; fetch and create the team from the source."""
    team = _committed_team(store)
    if team is not None:
        importer.set_target_team(team)
        return

    team_row = client.get_team()
    if Team.objects.filter(slug=team_row["slug"]).exists():
        raise CommandError(
            f"A team with slug '{team_row['slug']}' already exists locally but isn't tracked by this "
            "sync's state. Re-run with --force-delete to delete it and re-import from scratch."
        )
    importer.import_rows(TEAM_MODEL, [team_row])
    write("synced team")


def run_sync(
    client,
    store,
    private_key,
    write=lambda _m: None,
    page_limit=100,
    enforce_schema=True,
    on_user_created=send_password_reset_email,
    style=None,
):
    manifest = check_sync_preconditions(client, private_key, enforce_schema)

    importer = Importer(store, private_key=private_key, on_user_created=on_user_created)
    try:
        with mute_signals():
            load_team(importer, client, store, write)
            for entry in manifest["entries"]:
                model_label, resource, cursor_type = entry["model"], entry["resource"], entry["cursor"]
                model = entry_model(model_label)
                cursor = _start_cursor(model_label, cursor_type, store, model)
                count = importer.import_rows(
                    model_label, client.iter_rows(resource, start_cursor=cursor, limit=page_limit)
                )
                write(_style_synced_line(f"synced {count} {resource} rows", count, style))
    except requests.HTTPError as exc:
        friendly = _friendly_http_error_message(exc)
        if friendly is None:
            raise
        raise CommandError(friendly) from exc
    return importer


def force_delete_team(team_slug, state_dir, write=lambda _m: None):
    """Delete the local team (matched by slug) and its sync-state DB so the next run re-imports from
    scratch. Without resetting the state, the derived cursor would skip the rows that were deleted.

    Deletes via the same audited cascade the team-delete view uses, but without the notification
    emails -- nobody should be told their team was deleted during a re-import."""
    team = Team.objects.filter(slug=team_slug).first()
    if team is not None:
        with current_team(team):
            stats = delete_object_with_auditing_of_related_objects(team)
        write(f"force-deleted team '{team_slug}' and reset sync state ({sum(stats.values())} objects removed)")
    else:
        write(f"no local team '{team_slug}' to delete; reset sync state")
    Path(state_dir).joinpath(f"{team_slug}.sqlite").unlink(missing_ok=True)


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
        start_time = time.monotonic()
        private_key = None
        if options["private_key_path"]:
            private_key = load_private_key(Path(options["private_key_path"]).read_bytes())

        client = ResourceFetcher(options["source_url"], options["api_key"])
        enforce_schema = not options["skip_schema_check"]

        if options["force_delete"]:
            self._run_force_delete(options)

        store = FKTranslationStore(Path(options["state_dir"]) / f"{options['team_slug']}.sqlite")

        check_sync_preconditions(client, private_key, enforce_schema, store=store)

        run_sync(
            client,
            store,
            private_key,
            write=self.stdout.write,
            page_limit=options["limit"],
            enforce_schema=enforce_schema,
            style=self.style,
        )

        duration = timedelta(seconds=round(time.monotonic() - start_time))
        self._report(sync_complete=not store.has_unfilled_targets(), team_slug=options["team_slug"], duration=duration)

    def _run_force_delete(self, options):
        """Confirm and delete the local team plus its sync state."""
        if not self._confirm_force_delete(options["team_slug"]):
            raise CommandError("Aborted: --force-delete not confirmed.")
        force_delete_team(
            options["team_slug"],
            options["state_dir"],
            write=lambda message: self.stdout.write(self.style.WARNING(message)),
        )

    def _report(self, *, sync_complete: bool, team_slug: str, duration: timedelta | None = None) -> None:
        """Print everything the operator needs after a sync, so ``handle`` stays a thin wiring shell:
        which resources need manual setup, whether the sync finished or must be rerun, how long the
        run took, and the follow-up step for channel webhooks (a separate command -- see
        ``reregister_webhooks``). Sections are headed and blank-line separated so the report stands
        apart from the row-by-row progress log above it."""
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Sync report"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))

        if duration is not None:
            self.stdout.write(f"Duration: {duration}")

        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Channel webhooks were not re-registered."))
        self.stdout.write(
            f"  Run `manage.py reregister_webhooks --team-slug={team_slug}` to point this team's "
            "channel webhooks at this server."
        )

        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING("These resources are not migrated and must be recreated manually on this server:")
        )
        self.stdout.write("  - OAuth applications")
        self.stdout.write("  - Slack bots")
        self.stdout.write("  - User API keys")

        self.stdout.write("")
        if sync_complete:
            self.stdout.write(self.style.SUCCESS("Sync complete."))
        else:
            self.stdout.write(self.style.WARNING("Some rows are not yet synced; rerun to complete."))

    def _confirm_force_delete(self, team_slug) -> bool:
        self.stdout.write(
            self.style.WARNING(
                f"--force-delete will permanently delete the local team '{team_slug}' and all its data "
                "before re-importing."
            )
        )
        return _prompt("Type 'yes' to continue: ") == "yes"
