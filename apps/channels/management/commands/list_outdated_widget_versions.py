import csv
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, OuterRef, Subquery
from django.utils import timezone

from apps.channels import widget_versions
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessageType
from apps.experiments.models import ExperimentSession

DEFAULT_WINDOW_DAYS = 30


class Command(BaseCommand):
    help = (
        "List chatbots whose embedded chat widget is running an outdated version. "
        "Only channels used within the activity window are included, where 'used' means "
        "a session with at least one human message."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_WINDOW_DAYS,
            help=f"Activity window in days (default: {DEFAULT_WINDOW_DAYS})",
        )
        parser.add_argument("--team", type=str, help="Limit to a single team slug")
        parser.add_argument(
            "--format",
            type=str,
            choices=["table", "csv"],
            default="table",
            help="Output format: 'table' for console table, 'csv' for CSV output",
        )
        parser.add_argument(
            "--deprecated-only",
            action="store_true",
            help="Show only versions covered by a deprecation (excludes merely outdated ones)",
        )
        parser.add_argument(
            "--include-unreported",
            action="store_true",
            help=(
                "Include active channels that have never reported a version. These are excluded by "
                "default since their widget version cannot be determined."
            ),
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options["days"])
        rows = self._collect_rows(
            cutoff,
            team_slug=options["team"],
            deprecated_only=options["deprecated_only"],
            include_unreported=options["include_unreported"],
        )
        rows.sort(key=lambda row: (-row["session_count"], row["team"]))

        if options["format"] == "csv":
            self._output_csv(rows)
        else:
            self._output_table(rows, options["days"])

    def _collect_rows(self, cutoff, team_slug=None, deprecated_only=False, include_unreported=False) -> list[dict]:
        # Sessions that saw a human message inside the window; that message is what makes a
        # session count as real usage rather than a widget merely being loaded on a page.
        session_count = Subquery(
            ExperimentSession.objects.filter(
                experiment_channel=OuterRef("pk"),
                chat__messages__message_type=ChatMessageType.HUMAN,
                chat__messages__created_at__gte=cutoff,
            )
            .values("experiment_channel")
            .annotate(count=Count("id", distinct=True))
            .values("count")[:1]
        )
        channels = (
            ExperimentChannel.objects.filter(platform=ChannelPlatform.EMBEDDED_WIDGET)
            .select_related("experiment", "team")
            .annotate(session_count=session_count)
            .filter(session_count__gt=0)
        )
        if team_slug:
            channels = channels.filter(team__slug=team_slug)

        rows = []
        for channel in channels:
            version = channel.widget_version
            if version is None and not include_unreported:
                continue
            status, deprecation = _classify(version)
            if status is None:
                continue
            if deprecated_only and deprecation is None:
                continue
            rows.append(
                {
                    "team": channel.team.name,
                    "team_slug": channel.team.slug,
                    "chatbot": channel.experiment.name if channel.experiment else "",
                    "url": channel.experiment.get_absolute_url() if channel.experiment else "",
                    "channel_id": channel.id,
                    "version": version or "not reported",
                    "status": status,
                    "sunset_at": deprecation.sunset_at if deprecation else None,
                    "version_updated_at": channel.widget_version_updated_at,
                    "session_count": channel.session_count,
                }
            )
        return rows

    def _output_table(self, rows: list[dict], days: int) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Outdated chat widgets (latest: {widget_versions.LATEST_VERSION})"))
        self.stdout.write(f"Activity window: last {days} days, sessions with at least one human message")
        self.stdout.write("")

        if not rows:
            self.stdout.write(self.style.SUCCESS("No active chatbots are running an outdated widget version."))
            return

        header = (
            f"{'Team':<28} {'Chatbot':<32} {'Version':<12} {'Status':<11} "
            f"{'Sunset':<11} {'Last seen':<11} {'Sessions':>8}"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for row in rows:
            line = (
                f"{_truncate(row['team'], 28):<28} {_truncate(row['chatbot'], 32):<32} "
                f"{_truncate(row['version'], 12):<12} {row['status']:<11} "
                f"{_date(row['sunset_at']):<11} {_date(row['version_updated_at']):<11} "
                f"{row['session_count']:>8}"
            )
            self.stdout.write(self.style.WARNING(line) if row["sunset_at"] else line)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Summary:"))
        self.stdout.write(f"  Chatbots: {len(rows)}")
        self.stdout.write(f"  Teams: {len({row['team_slug'] for row in rows})}")
        self.stdout.write(f"  Sessions: {sum(row['session_count'] for row in rows)}")
        for status in ("sunset", "deprecated", "outdated"):
            count = sum(1 for row in rows if row["status"] == status)
            if count:
                self.stdout.write(f"  {status.title()}: {count}")
        self.stdout.write("")

    def _output_csv(self, rows: list[dict]) -> None:
        writer = csv.writer(self.stdout)
        writer.writerow(
            [
                "Team",
                "Team Slug",
                "Chatbot",
                "Chatbot URL",
                "Channel ID",
                "Widget Version",
                "Status",
                "Sunset At",
                "Version Last Reported",
                "Sessions",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["team"],
                    row["team_slug"],
                    row["chatbot"],
                    row["url"],
                    row["channel_id"],
                    row["version"],
                    row["status"],
                    _date(row["sunset_at"]),
                    _date(row["version_updated_at"]),
                    row["session_count"],
                ]
            )


def _classify(version: str | None) -> tuple[str | None, widget_versions.WidgetDeprecation | None]:
    """Categorise a recorded widget version. A None status means the version is current."""
    deprecation = widget_versions.get_deprecation(version)
    if deprecation:
        status = "sunset" if timezone.now() >= deprecation.sunset_at else "deprecated"
        return status, deprecation
    # An unreported or unparseable version predates the version header, so it is
    # older than every release even when no deprecation covers it.
    if version is None or widget_versions.clean_widget_version(version) is None:
        return "outdated", None
    if widget_versions.is_outdated(version):
        return "outdated", None
    return None, None


def _truncate(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 3] + "..."


def _date(value) -> str:
    return f"{timezone.localtime(value):%Y-%m-%d}" if value else "-"
