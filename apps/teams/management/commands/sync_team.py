"""Pull a team's data from a source Open Chat Studio server and recreate it locally.

    manage.py sync_team --source-url=<src> --api-key=<key> --team-slug=<slug> [--private-key-path=<path>]

The command is a thin shell: it wires the resource fetcher to the import engine and the local FK
translation store. Each run makes one pass over the manifest and exits; rerun to pick up new data.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.channels.models import ExperimentChannel
from apps.teams.export.client import ResourceFetcher
from apps.teams.export.emails import send_password_reset_email
from apps.teams.export.importer import Importer, mute_signals
from apps.teams.export.manifest import TEAM_MODEL, entry_model, schema_checksum
from apps.teams.export.seal import load_private_key
from apps.teams.export.translation import (
    FKTranslationStore,
    derive_pk_cursor,
    derive_updated_at_cursor,
)
from apps.teams.models import Team
from apps.teams.utils import current_team
from apps.utils.deletion import delete_object_with_auditing_of_related_objects

logger = logging.getLogger(__name__)

CHANNEL_SETUP_DOCS_URL = f"{settings.DOCUMENTATION_BASE_URL}{settings.DOCUMENTATION_LINKS['deploy_channels']}"


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


def check_sync_preconditions(client, private_key, enforce_schema=True) -> dict:
    """Fetch the source manifest and confirm the sync can actually proceed: the source is reachable,
    its schema matches, and we hold a key for any sealed secrets. Returns the manifest. Raises
    CommandError on any failure -- call this *before* any destructive local change (--force-delete)
    so a wrong key path, unreachable source, or schema mismatch can't leave the team deleted."""
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
    with mute_signals():
        importer.import_rows(TEAM_MODEL, [client.get_team()])
        write("synced team")
        for entry in manifest["entries"]:
            model_label, resource, cursor_type = entry["model"], entry["resource"], entry["cursor"]
            model = entry_model(model_label)
            cursor = _start_cursor(model_label, cursor_type, store, model)
            count = importer.import_rows(model_label, client.iter_rows(resource, start_cursor=cursor, limit=page_limit))
            write(_style_synced_line(f"synced {count} new {resource}", count, style))
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


@dataclass
class WebhookReregistrationReport:
    """Outcome of re-registering channel webhooks after a sync.

    ``updated``: labels of channels whose webhook was repointed automatically.
    ``manual``: (label, webhook_url) pairs for channels the operator must configure by hand.
    """

    updated: list[str]
    manual: list[tuple[str, str]]


def _channel_label(channel: ExperimentChannel) -> str:
    """Human label for the report: the chatbot name and platform. Team-scoped channels always have an
    experiment, so ``experiment`` is never null here (global channels, which lack one, have no team)."""
    return f"{channel.experiment.name} / {channel.platform_enum.label}"


def reregister_webhooks(team) -> WebhookReregistrationReport:
    """Repoint every one of the team's channel webhooks at this server via its WebhookManager.

    An imported channel's webhook at the provider still points at the source server; re-registering
    swaps in this server's ``webhook_url`` (built from the local site domain). This covers all of the
    team's channels rather than just the ones this run created: a sync interrupted partway through
    would otherwise lose track of which channels still need it. Re-registering an already-correct
    webhook is harmless, so covering the whole team is preferable to under-covering it. Channels whose
    provider can't manage webhooks -- or where the provider call fails -- are collected for manual
    setup rather than raising, so one bad channel can't fail the whole sync. Channels with no webhook
    to configure (web, API, evaluations, widget) are skipped."""
    report = WebhookReregistrationReport(updated=[], manual=[])
    channels = ExperimentChannel.objects.filter(team=team).select_related("experiment", "messaging_provider")
    for channel in channels:
        manager = channel.get_webhook_manager()
        if not manager or not manager.supports_webhook_management:
            if channel.webhook_url:  # a webhook is needed but we can't set it automatically
                report.manual.append((_channel_label(channel), channel.webhook_url))
            continue
        try:
            manager.set_incoming_webhook(channel.extra_data or {}, channel.webhook_url)
        except Exception:
            logger.exception("Error re-registering webhook for channel %s", channel.id)
            report.manual.append((_channel_label(channel), channel.webhook_url))
        else:
            report.updated.append(_channel_label(channel))
    return report


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
        parser.add_argument(
            "--noinput",
            "--no-input",
            action="store_false",
            dest="interactive",
            help="Skip the --force-delete confirmation prompt (for non-interactive runs).",
        )

    def handle(self, *args, **options):
        private_key = None
        if options["private_key_path"]:
            private_key = load_private_key(Path(options["private_key_path"]).read_bytes())

        client = ResourceFetcher(options["source_url"], options["api_key"])
        enforce_schema = not options["skip_schema_check"]

        # Preflight before the destructive --force-delete: a bad key path (read above), an
        # unreachable source, or a schema mismatch must fail while the existing team is still intact.
        check_sync_preconditions(client, private_key, enforce_schema)

        if options["force_delete"]:
            if options.get("interactive", True) and not self._confirm_force_delete(options["team_slug"]):
                raise CommandError("Aborted: --force-delete not confirmed.")
            force_delete_team(
                options["team_slug"],
                options["state_dir"],
                write=lambda message: self.stdout.write(self.style.WARNING(message)),
            )

        store = FKTranslationStore(Path(options["state_dir"]) / f"{options['team_slug']}.sqlite")

        importer = run_sync(
            client,
            store,
            private_key,
            write=self.stdout.write,
            page_limit=options["limit"],
            enforce_schema=enforce_schema,
            style=self.style,
        )

        report = reregister_webhooks(importer.target_team)
        self._report(report, sync_complete=not store.has_unfilled_targets())

    def _report(self, webhook_report: WebhookReregistrationReport, *, sync_complete: bool) -> None:
        """Print everything the operator needs after a sync, so ``handle`` stays a thin wiring shell:
        which channel webhooks were repointed automatically, which channels and un-synced resources
        need manual setup, and whether the sync finished or must be rerun. Sections are headed and
        blank-line separated so the report stands apart from the row-by-row progress log above it."""
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Sync report"))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 60))

        if webhook_report.updated:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("Re-registered webhooks automatically for:"))
            for label in webhook_report.updated:
                self.stdout.write(f"  - {label}")

        if webhook_report.manual:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("These channels need their webhooks re-registered manually:"))
            for label, url in webhook_report.manual:
                self.stdout.write(f"  - {label}")
                self.stdout.write(f"      {url}")
            self.stdout.write(f"  See {CHANNEL_SETUP_DOCS_URL} for channel setup instructions.")

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
        return input("Type 'yes' to continue: ") == "yes"
