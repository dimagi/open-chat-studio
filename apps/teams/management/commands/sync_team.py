"""Pull a team's data from a source Open Chat Studio server and recreate it locally.

    manage.py sync_team --source-url=<src> --api-key=<key> --team-slug=<slug> [--private-key-path=<path>]

The command is a thin shell: it wires the resource fetcher to the import engine and the local FK
translation store. Each run makes one pass over the manifest and exits.

It is meant for a one-off migration, not an ongoing sync. For the cutover, run it with ``--follow``:
after the first pass it keeps reading new rows from the source every ``--interval`` seconds until
stopped with Ctrl+C. While it runs, ``reregister_webhooks`` in another shell points the team's
channels at this server; once every channel is switched and the passes stop finding new rows, stop
the sync. ``--force-delete`` wipes the local team and its sync state so the import starts from
scratch.

The source decides how much of the team moves: a team admin there can limit the export to selected
chatbots. The command has no chatbot flag; it syncs whatever the source allows and reports it.

Every user the sync creates is sent a password-reset email, since passwords are hashed and never
migrated. A failed send doesn't stop the sync; the addresses are listed in the sync report instead,
to be followed up by hand.

Run this against the latest code on both the source and target servers. Compatibility is verified
by comparing checksums of the two servers' export OpenAPI schemas -- the exact shape of the data
being transferred -- rather than their migration history, which is a poor proxy (a squash or rename
changes the recorded migrations without changing the schema). A mismatch means the servers run
different export code; deploy the latest code on both, or pass --skip-schema-check to override.
"""

import contextlib
import itertools
import os
import signal
import threading
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from types import FrameType
from typing import Any

import requests
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.core.management.color import Style
from django.db import close_old_connections

from apps.teams.export.client import ResourceFetcher
from apps.teams.export.emails import send_password_reset_email
from apps.teams.export.importer import Importer, UnresolvedForeignKey, mute_signals
from apps.teams.export.manifest import TEAM_MODEL, schema_checksum
from apps.teams.export.progress import SyncProgress, timestamped
from apps.teams.export.seal import MISSING_PUBLIC_KEY_DETAIL, load_private_key
from apps.teams.export.selection import SELECTION_CHANGED_DETAIL
from apps.teams.export.translation import (
    ALL_CHATBOTS_KEY,
    FKTranslationStore,
    page_cursor,
    selection_key,
)
from apps.teams.models import Team
from apps.teams.utils import current_team
from apps.users.models import CustomUser
from apps.utils.deletion import delete_object_with_auditing_of_related_objects

FILES_CONFIRMED_FLAG = "files_confirmed"
PRIVATE_KEY_ENV_VAR = "SYNC_TEAM_PRIVATE_KEY"
MIGRATION_MODE_REQUIRED = "Migration mode needs to be enabled on the source team before you can continue."
MISSING_PUBLIC_KEY_MESSAGE = (
    "The source team has no public key registered, so its secret data cannot be exported. "
    "Set the team's public key on the source server before syncing."
)
SELECTION_CHANGED_MESSAGE = (
    "The source team's chatbot selection changed during the sync, so the run stopped before importing "
    "rows from two selections. Rerun to sync the new selection."
)

# Known source-server refusals, matched by status code and detail marker, with the friendly
# message to show the operator instead of a raw HTTP traceback.
_FRIENDLY_HTTP_ERRORS = (
    (
        400,
        MISSING_PUBLIC_KEY_DETAIL,
        MISSING_PUBLIC_KEY_MESSAGE,
    ),
    (
        409,
        SELECTION_CHANGED_DETAIL,
        SELECTION_CHANGED_MESSAGE,
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


def _load_private_key(private_key_path: str | None):
    """Load the RSA private key used to unseal secret fields, or None when no key is configured.

    The --private-key-path flag points to a key file and takes precedence. When it is omitted, the
    SYNC_TEAM_PRIVATE_KEY env var is read for the key contents themselves (PEM), not a path to it."""
    if private_key_path:
        return load_private_key(Path(private_key_path).read_bytes())
    env_key = os.environ.get(PRIVATE_KEY_ENV_VAR)
    if env_key:
        return load_private_key(env_key.encode())
    return None


def check_source_team_ready(client, write=lambda _m: None) -> dict:
    """Block the sync unless the source team is in migration mode and has a public key registered --
    both must be set. The export API no longer enforces migration mode server-side, so the client
    checks it here from the team endpoint's ``is_migrating`` / ``has_public_key`` status (the latter is a
    boolean saying whether a key is registered). Also prints what the source says it will export, so
    the operator sees the server's answer rather than assuming. Raises CommandError listing whatever
    is missing. Returns the source's team payload."""
    team = client.get_team()
    problems = []
    if not team.get("is_migrating"):
        problems.append(MIGRATION_MODE_REQUIRED)
    if not team.get("has_public_key"):
        problems.append(MISSING_PUBLIC_KEY_MESSAGE)
    if problems:
        raise CommandError(" ".join(problems))

    chatbots = team.get("exportable_chatbots") or []
    if chatbots:
        write(f"The source will export {len(chatbots)} of this team's chatbots:")
        for chatbot in chatbots:
            write(f"  - {chatbot['name']}")
    else:
        write("The source will export the whole team.")
    return team


def _prompt(message: str) -> str:
    """input() that turns EOF (no terminal -- cron, CI, piped stdin) into a clean abort."""
    try:
        return input(message)
    except EOFError:
        raise CommandError("This command must be run interactively; stdin is closed.") from None


def check_sync_preconditions(client, private_key, enforce_schema=True, store=None, write=lambda _m: None) -> dict:
    """Fetch the source manifest and confirm the sync can actually proceed: the source is reachable,
    its export schema matches ours, we hold a key for any sealed secrets, and the source team is
    ready to export (migration mode on, public key set). When a ``store`` is given and the source
    exports the whole team, also ask the operator to confirm the team's files were moved to this
    server's storage backend -- that happens outside this command and the sync fails without it. A
    partial sync skips the question: its files are fetched from the source one by one. The answer is
    recorded in the store only once every check passes, so an aborted run asks again while a rerun
    after a clean preflight doesn't. Returns the manifest. Raises CommandError on any failure, before
    any rows are imported."""

    manifest = client.get_manifest()
    if enforce_schema and manifest.get("schema_checksum") != schema_checksum():
        raise CommandError(
            "Source and target export schema checksums differ, so the servers are running different "
            "export code. Deploy the same (latest) code on both servers before syncing, or pass "
            "--skip-schema-check to override."
        )

    if private_key is None and any(entry.get("secret") for entry in manifest["entries"]):
        raise CommandError(
            "The source exports sealed secret fields but no private key was provided; the secrets "
            "would be imported as unreadable tokens. Pass --private-key-path with the team's key."
        )

    team = check_source_team_ready(client, write)

    if team.get("exportable_chatbots"):
        # The files export bundles a whole team, so there is nothing for the operator to have moved.
        # Importer._handle_missing_object fetches each blob from the source as its row lands, and the
        # report lists the ones the source had no content for.
        write("Files for the selected chatbots will be backfilled from the source as they are needed.")
        return manifest

    if store is not None and not store.has_flag(FILES_CONFIRMED_FLAG):
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
        store.set_flag(FILES_CONFIRMED_FLAG)
    return manifest


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


def load_team(importer, client, store):
    """Set the importer's target team, doing the least work needed to anchor the sync.

    Three cases, by where the team is already known:
    - Recorded in the sync store: a prior run imported it, so load it straight from the target DB --
      no source fetch, so the ``team/`` endpoint (and the source migration lock) isn't hit again.
    - Present in the target DB under the same slug but not in the store: a team this sync doesn't track
      (created by hand, or its state DB was lost). Refuse to import over it and tell the operator to
      re-run with --force-delete, rather than silently overwriting a team we didn't create.
    - Neither: a first-time import; fetch and create the team from the source, then put the new team
      into migration mode so this server freezes its outbound firing too (see
      ``_enable_target_migration_mode``)."""
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
    _enable_target_migration_mode(importer.target_team)


def _enable_target_migration_mode(team: Team) -> None:
    """Freeze the freshly imported team on this server by turning on migration mode. The source team is
    already migrating; without this the target would start firing the team's events and scheduled
    messages the moment it's imported, duplicating what the still-live source sends. Migration mode is
    a per-server operational flag (not exported), so it's set here on creation and stays on until an
    operator turns it off at cutover -- reruns load the team from the sync state and leave it alone."""
    team.is_migrating = True
    team.save(update_fields=["is_migrating"])


def run_sync(
    client: ResourceFetcher,
    store: FKTranslationStore,
    private_key: RSAPrivateKey | None,
    write: Callable[[str], None] = lambda _m: None,
    page_limit: int = 100,
    enforce_schema: bool = True,
    on_user_created: Callable[[CustomUser], None] | None = send_password_reset_email,
    style: Style | None = None,
    follow: bool = False,
    interval: int = 10,
    stop: threading.Event | None = None,
) -> Importer:
    """Sync the team once, or with ``follow`` keep syncing every ``interval`` seconds until ``stop``
    (a ``threading.Event``) is set. A stop takes effect between pages, so every page read is committed
    with its cursor."""
    stop = stop or threading.Event()
    manifest = check_sync_preconditions(client, private_key, enforce_schema)
    cursor_key, chatbots = resolve_selection(client, store)
    progress = SyncProgress(write=write, entries=manifest["entries"], style=style)

    importer = Importer(
        store,
        private_key=private_key,
        on_user_created=on_user_created,
        fetch_file_content=client.get_file_content,
    )
    sync_pass = _SyncPass(
        importer=importer,
        client=client,
        store=store,
        entries=manifest["entries"],
        page_limit=page_limit,
        cursor_key=cursor_key,
        under_selection=bool(chatbots),
        progress=progress,
        stop=stop,
    )
    try:
        with mute_signals():
            load_team(importer, client, store)
            first_pass = sync_pass.run(first_pass=True)
            if follow:
                _follow(
                    sync_pass,
                    first_pass_count=sum(count for _entry, count in first_pass),
                    interval=interval,
                    stop=stop,
                    progress=progress,
                )
    except requests.HTTPError as exc:
        friendly = _friendly_http_error_message(exc)
        if friendly is None:
            raise
        raise CommandError(friendly) from exc
    return importer


def _follow(
    sync_pass: "_SyncPass",
    first_pass_count: int,
    interval: int,
    stop: threading.Event,
    progress: SyncProgress,
) -> None:
    """After the first pass, run a pass every ``interval`` seconds until ``stop`` is set. A pass that
    was stopped part-way gets no summary, since its entry already reported the stop."""
    last_new_row_at = time.monotonic()
    count = first_pass_count
    for number in itertools.count(start=1):
        if number > 1:
            progress.pass_started(number)
            close_old_connections()
            count = sum(count for _entry, count in sync_pass.run(first_pass=False))
        if stop.is_set():
            return
        now = time.monotonic()
        if count:
            last_new_row_at = now
        quiet_for = timedelta(seconds=round(now - last_new_row_at))
        progress.pass_finished(number=number, count=count, quiet_for=quiet_for, next_pass_in=interval)
        if stop.wait(interval):
            return


def resolve_selection(client, store) -> tuple[str, list[dict]]:
    """The cursor namespace for this run, and the chatbots the source says it will serve.

    The selection is the source's own allowlist, so it can change between runs. A narrower selection
    reuses the cursors of any wider one already synced, including a full-team sync, because the source
    then serves a subset of the same rows. A wider one has rows below those cursors that were never
    fetched, so it starts from the beginning.
    """
    chatbots = client.get_team().get("exportable_chatbots") or []
    public_ids = [chatbot["public_id"] for chatbot in chatbots]
    key = selection_key(public_ids)
    if key != ALL_CHATBOTS_KEY and not store.cursors_for(key):
        selected = set(public_ids)
        wider = [other for other, ids in store.selections().items() if other != key and selected <= set(ids)]
        if store.cursors_for(ALL_CHATBOTS_KEY):
            wider.append(ALL_CHATBOTS_KEY)
        for other in wider:
            store.seed_cursors_from(other, key)
    store.record_selection(key, public_ids)
    return key, chatbots


class _SyncPass:
    """One read of every manifest entry, with catch-up reads for parents the source created while the
    pass was running.

    The source keeps writing while the sync runs, so a row can reference a parent created after the
    parent's resource was read. The parent already exists on the source, so re-reading its resource
    from the cursor fetches it. Under a selection, a resource flagged ``reread_under_selection`` can
    also gain an old row below its cursor; when the first catch-up does not find the parent, such a
    resource is read again from the beginning. Only entries earlier than the one being read can be
    caught up, so the recursion ends.
    """

    def __init__(
        self,
        importer: Importer,
        client: ResourceFetcher,
        store: FKTranslationStore,
        entries: list[dict],
        page_limit: int,
        cursor_key: str,
        under_selection: bool,
        progress: SyncProgress,
        stop: threading.Event,
    ) -> None:
        self.importer = importer
        self.client = client
        self.store = store
        self.entries = entries
        self.page_limit = page_limit
        self.cursor_key = cursor_key
        self.under_selection = under_selection
        self.progress = progress
        self.stop = stop

    def run(self, first_pass: bool) -> list[tuple[dict, int]]:
        """Read every entry and return each entry read with its synced row count. Only the first pass of
        a run reads the resources flagged ``reread_under_selection`` from the beginning; later passes
        rely on catch-up for an old row that joins the selection. Stops early once ``stop`` is set, so
        the result can be shorter than the entries."""
        synced = []
        for position, entry in enumerate(self.entries):
            if self.stop.is_set():
                break
            cursor = self._start_cursor(position=position, reread=first_pass and self._rereadable(entry))
            self.progress.entry_started(position=position, from_start=cursor is None)
            count = self._read(position=position, cursor=cursor, stoppable=True)
            self.progress.entry_finished(position=position, count=count, stopped=self.stop.is_set())
            synced.append((entry, count))
        return synced

    def _rereadable(self, entry: dict) -> bool:
        return self.under_selection and entry["reread_under_selection"]

    def _start_cursor(self, position: int, reread: bool) -> str | None:
        """Where a read of the entry starts: its stored cursor, or the beginning (None) for a reread."""
        if reread:
            return None
        return self.store.get_cursor(self.cursor_key, self.entries[position]["model"])

    def _read(self, position: int, cursor: str | None, stoppable: bool) -> int:
        """Import one entry page by page from ``cursor``, recording the resume cursor once each page's
        rows are committed. The cursor is stored rather than derived from the synced rows, because the
        row set the source serves changes with the chatbot selection. Rows already imported at the same
        source revision are skipped by the importer. A ``stoppable`` read ends after the current page
        once ``stop`` is set."""
        entry = self.entries[position]
        model_label, cursor_type = entry["model"], entry["cursor"]
        count = 0
        pages = self.client.iter_pages(
            entry["resource"], start_cursor=cursor, limit=self.page_limit, selection=self.cursor_key
        )
        for number, rows in enumerate(pages, start=1):
            page_count = sum(self._import_row(position=position, row=row) for row in rows)
            count += page_count
            self.progress.page_read(position=position, number=number, fetched=len(rows), synced=page_count, total=count)
            next_cursor = page_cursor(cursor_type, rows)
            if next_cursor is not None:
                self.store.set_cursor(self.cursor_key, model_label, next_cursor)
            if stoppable and self.stop.is_set():
                break
        return count

    def _import_row(self, position: int, row: dict) -> int:
        """Import one row. When it points at a parent the sync has not seen yet, run the next catch-up
        read for that parent and retry. A parent still missing once every catch-up has run is outside
        the export (deleted, or not in the selection), so the error is raised. A catch-up ignores the
        stop request, since the row waiting on it would otherwise fail."""
        catch_ups: dict[str | None, Iterator[tuple[int, bool]]] = {}
        while True:
            try:
                return self.importer.import_rows(self.entries[position]["model"], [row])
            except UnresolvedForeignKey as exc:
                reads = catch_ups.setdefault(exc.target_label, self._catch_up_reads(position, exc.target_label))
                if (read := next(reads, None)) is None:
                    raise
                parent_position, reread = read
                self.progress.catching_up(
                    position=position, error=exc, parent_position=parent_position, from_start=reread
                )
                cursor = self._start_cursor(position=parent_position, reread=reread)
                count = self._read(position=parent_position, cursor=cursor, stoppable=False)
                self.progress.caught_up(parent_position=parent_position, count=count)

    def _catch_up_reads(self, before: int, model_label: str | None) -> Iterator[tuple[int, bool]]:
        """The reads that can bring in a missing ``model_label`` row, in order, as (position, reread):
        its entry from the cursor, then from the beginning when it is rereadable. Only an entry before
        ``before`` qualifies."""
        for position, entry in enumerate(self.entries[:before]):
            if entry["model"] == model_label:
                yield position, False
                if self._rereadable(entry):
                    yield position, True
                return


def force_delete_team(team_slug, state_dir, write=lambda _m: None):
    """Delete the local team (matched by slug) and its sync-state DB so the next run re-imports from
    scratch. Without resetting the state, the stored cursors would skip the rows that were deleted.

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

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--source-url", required=True)
        parser.add_argument("--api-key", required=True)
        parser.add_argument(
            "--team-slug",
            required=True,
            help="Names the local run state DB and identifies the team to drop when --force-delete is set.",
        )
        parser.add_argument(
            "--private-key-path",
            help=(
                "Path to the RSA private key file used to unseal secret fields. When omitted, the "
                f"key is read from the {PRIVATE_KEY_ENV_VAR} env var, which holds the key itself "
                "(PEM contents) rather than a path. The flag takes precedence over the env var."
            ),
        )
        parser.add_argument("--state-dir", default=".", help="Directory for the per-team SQLite state DB.")
        parser.add_argument("--limit", type=int, default=100, help="Page size for resource requests.")
        parser.add_argument(
            "--skip-schema-check",
            action="store_true",
            help="Sync even if the source and target export schema checksums differ.",
        )
        parser.add_argument(
            "--follow",
            action="store_true",
            help=(
                "Keep syncing until stopped with Ctrl+C, for switching channels over while the source is "
                "still receiving messages. The stop takes effect after the current page."
            ),
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=10,
            help="Seconds to wait between passes with --follow.",
        )
        parser.add_argument(
            "--force-delete",
            action="store_true",
            help="Delete the local team and its sync state before syncing, for a clean re-import.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        start_time = time.monotonic()
        private_key = _load_private_key(options["private_key_path"])

        progress = timestamped(self.stdout.write)
        client = ResourceFetcher(
            options["source_url"],
            options["api_key"],
            on_retry=lambda message: progress(self.style.WARNING(message)),
        )
        enforce_schema = not options["skip_schema_check"]

        if options["force_delete"]:
            self._run_force_delete(options, client)

        with FKTranslationStore(Path(options["state_dir"]) / f"{options['team_slug']}.sqlite") as store:
            check_sync_preconditions(
                client, private_key, enforce_schema=enforce_schema, store=store, write=self.stdout.write
            )

            stop = threading.Event()
            interrupt = self._stop_on_interrupt(stop, write=progress) if options["follow"] else contextlib.nullcontext()
            with interrupt:
                importer = run_sync(
                    client,
                    store,
                    private_key,
                    write=progress,
                    page_limit=options["limit"],
                    enforce_schema=enforce_schema,
                    style=self.style,
                    follow=options["follow"],
                    interval=options["interval"],
                    stop=stop,
                )

            duration = timedelta(seconds=round(time.monotonic() - start_time))
            self._report(
                sync_complete=not store.has_unfilled_targets(),
                team_slug=options["team_slug"],
                duration=duration,
                missing_files=importer.missing_files,
                notification_failures=importer.notification_failures,
                chatbots=client.get_team().get("exportable_chatbots") or [],
                skipped_rows=importer.skipped_rows,
                adopted_rows=importer.adopted_rows,
                kept_target_edits=importer.kept_target_edits,
            )

    @contextlib.contextmanager
    def _stop_on_interrupt(self, stop: threading.Event, write: Callable[[str], None]) -> Iterator[None]:
        """Turn the first Ctrl+C (or SIGTERM) into a request to stop after the current page, so the
        sync ends with every page it read committed and the report printed. A second Ctrl+C or SIGTERM
        aborts at once."""

        def request_stop(signum: int, frame: FrameType | None) -> None:
            write(self.style.WARNING("Stopping after the current page. Press Ctrl+C again to abort immediately."))
            stop.set()
            signal.signal(signal.SIGINT, signal.default_int_handler)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)

        previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def _run_force_delete(self, options, client):
        """Confirm and delete the local team plus its sync state."""
        partial = bool(client.get_team().get("exportable_chatbots"))
        if not self._confirm_force_delete(options["team_slug"], partial=partial):
            raise CommandError("Aborted: --force-delete not confirmed.")
        force_delete_team(
            options["team_slug"],
            options["state_dir"],
            write=lambda message: self.stdout.write(self.style.WARNING(message)),
        )

    def _report(
        self,
        *,
        sync_complete: bool,
        team_slug: str,
        duration: timedelta | None = None,
        missing_files: Sequence[str] = (),
        notification_failures: Sequence[tuple[str, str]] = (),
        chatbots: Sequence[dict] = (),
        skipped_rows: int = 0,
        adopted_rows: Mapping[str, Collection[int]] | None = None,
        kept_target_edits: Mapping[str, Collection[int]] | None = None,
    ) -> None:
        """Print everything the operator needs after a sync, so ``handle`` stays a thin wiring shell:
        which chatbots moved, which resources need manual setup, which files the source had no content
        for, whether the sync finished or must be rerun, how long the run took, and the follow-up step
        for channel webhooks (a separate command -- see ``reregister_webhooks``). Sections are headed
        and blank-line separated so the report stands apart from the row-by-row progress log above it."""
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Sync report"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))

        if duration is not None:
            self.stdout.write(f"Duration: {duration}")
        if skipped_rows:
            self.stdout.write(f"Rows already up to date and left untouched: {skipped_rows}")
        if adopted_rows:
            self._report_model_counts(
                "These rows already existed on this server, created after a channel was switched over. "
                "The source rows were linked to them and the rows on this server were kept as they are:",
                adopted_rows,
            )
        if kept_target_edits:
            self._report_model_counts(
                "These rows were changed on this server after they were synced, so later changes to them "
                "on the source were not applied:",
                kept_target_edits,
            )

        if chatbots:
            self._report_chatbots(chatbots)
        if missing_files:
            self._report_missing_files(missing_files)
        if notification_failures:
            self._report_notification_failures(notification_failures)

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

    def _report_chatbots(self, chatbots: Sequence[dict]) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Chatbots synced"))
        for chatbot in chatbots:
            self.stdout.write(f"  - {chatbot['name']}")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "Only these chatbots were migrated. Evaluations, human annotations and transcript "
                "analyses were not migrated for them, and neither was anything belonging to this "
                "team's other chatbots. Team members, tags, pricing rules and notifications were "
                "migrated for the whole team, and notifications can mention the other chatbots."
            )
        )

    def _report_model_counts(self, heading: str, source_pks: Mapping[str, Collection[int]]) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.WARNING(heading))
        for model_label, pks in source_pks.items():
            self.stdout.write(f"  - {model_label}: {len(pks)}")

    def _report_missing_files(self, missing_files: Sequence[str]) -> None:
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                f"{len(missing_files)} file(s) had no content on the source and were imported without it:"
            )
        )
        for name in missing_files:
            self.stdout.write(f"  - {name}")

    def _report_notification_failures(self, notification_failures: Sequence[tuple[str, str]]) -> None:
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(f"{len(notification_failures)} user(s) could not be sent a password-reset email:")
        )
        for identifier, error in notification_failures:
            self.stdout.write(f"  - {identifier}: {error}")
        self.stdout.write("  They were imported, and a rerun won't retry the email; send theirs by hand.")

    def _confirm_force_delete(self, team_slug, partial) -> bool:
        warning = (
            f"--force-delete will permanently delete the local team '{team_slug}' and all its data "
            "before re-importing. This also removes the team's files from backend storage"
        )
        if partial:
            warning += "; the sync fetches them from the source again."
        else:
            warning += ", so they must be re-imported after the delete completes."
        self.stdout.write(self.style.WARNING(warning))
        return _prompt("Type 'yes' to continue: ") == "yes"
