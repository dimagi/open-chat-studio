from datetime import timedelta

from django.utils import timezone

from apps.channels import widget_versions
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.experiments.models import ExperimentSession
from apps.ocs_notifications.notifications import deprecated_widget_version_notification

# Widgets older than 0.5.1 send no version header; "recently active" channels
# with no recorded version are assumed to be running one of those.
RECENT_ACTIVITY_WINDOW = timedelta(days=90)


class Command(IdempotentCommand):
    help = "Notify teams whose embedded chat widgets run a deprecated version"
    # Fixed slug: each new deprecation ships its own Django data migration
    # (RunDataMigration with force=True), so this never needs bumping.
    # See docs/developer_guides/widget_versioning.md
    migration_name = "notify_deprecated_widget_versions"
    disable_audit = True

    def perform_migration(self, dry_run=False):
        if not widget_versions.DEPRECATIONS:
            self.stdout.write(self.style.SUCCESS("No widget deprecations configured"))
            return

        affected_by_team = self._collect_affected_teams()
        if not affected_by_team:
            self.stdout.write(self.style.SUCCESS("No teams affected by widget deprecations"))
            return

        self.stdout.write(f"Found {len(affected_by_team)} affected team(s)")
        if self.verbosity > 1:
            self._print_details(affected_by_team)

        if dry_run:
            self.stdout.write(f"Would notify {len(affected_by_team)} team(s)")
            return

        for data in affected_by_team.values():
            deprecated_widget_version_notification(
                team=data["team"],
                affected_chatbots=data["chatbots"],
                versions=data["versions"],
                sunset_at=data["sunset_at"],
                latest_version=widget_versions.LATEST_VERSION,
                docs_url=data["docs_url"],
            )

        self.stdout.write(self.style.SUCCESS(f"Notified {len(affected_by_team)} team(s)"))
        return f"Notified {len(affected_by_team)} team(s)"

    def _collect_affected_teams(self) -> dict:
        # Notify under the most recent deprecation only: a channel deprecated by an
        # earlier entry is re-notified under the newest (highest-version) one.
        deprecation = widget_versions.latest_deprecation()
        if deprecation is None:
            return {}

        cutoff = timezone.now() - RECENT_ACTIVITY_WINDOW
        channels = ExperimentChannel.objects.filter(
            platform=ChannelPlatform.EMBEDDED_WIDGET, deleted=False
        ).select_related("experiment", "team")
        channels_with_recent_sessions = set(
            ExperimentSession.objects.filter(experiment_channel__in=channels, created_at__gte=cutoff).values_list(
                "experiment_channel_id", flat=True
            )
        )

        affected_by_team = {}
        for channel in channels:
            if not widget_versions.is_deprecated(channel.widget_version, deprecation):
                continue
            if channel.widget_version is None and channel.id not in channels_with_recent_sessions:
                continue
            self._add_affected_channel(affected_by_team, channel, deprecation)
        return affected_by_team

    @staticmethod
    def _add_affected_channel(affected_by_team: dict, channel, deprecation) -> None:
        team_data = affected_by_team.setdefault(
            channel.team_id,
            {
                "team": channel.team,
                "chatbots": {},
                "versions": set(),
                "sunset_at": deprecation.sunset_at,
                "docs_url": deprecation.docs_url,
            },
        )
        team_data["chatbots"][channel.experiment.name] = channel.experiment.get_absolute_url()
        team_data["versions"].add(channel.widget_version or "unknown")

    def _print_details(self, affected_by_team: dict) -> None:
        for data in affected_by_team.values():
            self.stdout.write(f"  Team: {data['team'].name}")
            self.stdout.write(f"    Versions: {sorted(data['versions'])}")
            self.stdout.write(f"    Chatbots: {sorted(data['chatbots'])}")
