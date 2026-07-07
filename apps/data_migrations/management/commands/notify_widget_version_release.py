from apps.channels import widget_versions
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.ocs_notifications.notifications import widget_version_release_notification


class Command(IdempotentCommand):
    help = "Notify teams that a new embedded chat widget version is available"
    # Fixed slug: each release ships its own Django data migration
    # (RunDataMigration with force=True), so this never needs bumping.
    # See docs/developer_guides/widget_versioning.md
    migration_name = "notify_widget_version_release"
    disable_audit = True

    def add_arguments(self, parser):
        super().add_arguments(parser)
        # "--version" is reserved by Django's BaseCommand, hence "--widget-version".
        parser.add_argument("--widget-version", default=widget_versions.LATEST_VERSION)
        parser.add_argument("--notes", default="")
        parser.add_argument("--changelog-url", default="")

    def handle(self, *args, **options):
        self.widget_version = options["widget_version"]
        self.notes = options.get("notes") or ""
        self.changelog_url = options.get("changelog_url") or widget_versions.widget_docs_url()
        super().handle(*args, **options)

    def perform_migration(self, dry_run=False):
        affected_by_team = self._collect_affected_teams()
        if not affected_by_team:
            self.stdout.write(self.style.SUCCESS("No teams use the embedded chat widget"))
            return

        self.stdout.write(f"Found {len(affected_by_team)} team(s) using the embedded widget")
        if self.verbosity > 1:
            self._print_details(affected_by_team)

        if dry_run:
            self.stdout.write(f"Would notify {len(affected_by_team)} team(s) about widget {self.widget_version}")
            return

        for data in affected_by_team.values():
            widget_version_release_notification(
                team=data["team"],
                version=self.widget_version,
                notes=self.notes,
                changelog_url=self.changelog_url,
                affected_chatbots=data["chatbots"],
            )

        self.stdout.write(self.style.SUCCESS(f"Notified {len(affected_by_team)} team(s)"))
        return f"Notified {len(affected_by_team)} team(s)"

    def _collect_affected_teams(self) -> dict:
        channels = (
            ExperimentChannel.objects.filter(platform=ChannelPlatform.EMBEDDED_WIDGET, deleted=False)
            .select_related("experiment", "team")
            .only(
                "team__slug",
                "team__name",
                "experiment__name",
                "experiment__team",
                "experiment__working_version",
                "experiment__version_number",
            )
        )

        affected_by_team = {}
        for channel in channels:
            team_data = affected_by_team.setdefault(
                channel.team_id,
                {"team": channel.team, "chatbots": {}},
            )
            team_data["chatbots"][channel.experiment.name] = channel.experiment.get_absolute_url()
        return affected_by_team

    def _print_details(self, affected_by_team: dict) -> None:
        for data in affected_by_team.values():
            self.stdout.write(f"  Team: {data['team'].name}")
            self.stdout.write(f"    Chatbots: {sorted(data['chatbots'])}")
