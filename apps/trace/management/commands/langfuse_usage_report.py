import csv
import io
import json
from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import BooleanField, Count, Max
from django.db.models.expressions import RawSQL
from django.utils import timezone

from apps.service_providers.models import TraceProvider
from apps.trace.models import Trace

DEFAULT_HOST = "https://langfuse.openchatstudio.com/"

# Matches a Trace whose trace_metadata has a langfuse trace_info element with a trace_url
# starting at the requested host. trace_metadata = {"trace_info": [{"trace_provider", "trace_url"}]}.
LANGFUSE_HOST_JSONPATH = '$.trace_info[*] ? (@.trace_provider == "langfuse" && @.trace_url starts with $h)'


class Command(BaseCommand):
    help = "Report teams/experiments referencing a Langfuse trace provider for a base URL, with recent usage counts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--host",
            type=str,
            default=DEFAULT_HOST,
            help=f"Langfuse base URL to match (trailing slash ignored). Default: {DEFAULT_HOST}",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Window (in days) for counting recent trace usage. Default: 30",
        )
        parser.add_argument(
            "--csv",
            action="store_true",
            help="Output one CSV row per experiment reference to stdout (redirect to a file).",
        )

    def handle(self, *args, **options):
        host = options["host"].rstrip("/")
        days = options["days"]
        since = timezone.now() - timedelta(days=days)

        # 1. Configured providers (config is encrypted -> filter host in Python).
        providers = [
            tp
            for tp in TraceProvider.objects.filter(type="langfuse").select_related("team")
            if (tp.config.get("host") or "").rstrip("/") == host
        ]

        # 2. Recent trace usage, matched on the (unencrypted) trace_url in trace_metadata.
        #    trace_metadata = {"trace_info": [{"trace_provider": "langfuse", "trace_url": "<host>/..."}]}
        usage = self._recent_usage(host, since)

        rows = self._build_rows(providers, usage, days)
        if options["csv"]:
            self._write_csv(rows)
        else:
            self._print_report(host, days, providers, rows, usage)

    def _recent_usage(self, host, since):
        """Return {experiment_id: {"count": int, "last": datetime}} for recent Langfuse traces on this host."""
        match = RawSQL(
            "jsonb_path_exists(trace_metadata, %s::jsonpath, %s::jsonb)",
            [LANGFUSE_HOST_JSONPATH, json.dumps({"h": f"{host}/"})],
            output_field=BooleanField(),
        )
        rows = (
            Trace.objects.filter(timestamp__gte=since)
            .annotate(matches_host=match)
            .filter(matches_host=True)
            .values("experiment_id")
            .annotate(count=Count("id"), last=Max("timestamp"))
        )
        return {r["experiment_id"]: {"count": r["count"], "last": r["last"]} for r in rows}

    def _build_rows(self, providers, usage, days):
        """Flatten providers/experiments into one dict per experiment reference (shared by both renderers).

        Providers with no experiments still emit a single row with empty experiment fields so that
        configured-but-unused providers remain visible.
        """
        rows = []
        for tp in sorted(providers, key=lambda p: (p.team.slug, p.id)):
            experiments = list(tp.experiment_set.values_list("id", "name", "is_archived")) or [(None, "", None)]
            for eid, ename, archived in experiments:
                u = usage.get(eid, {"count": 0, "last": None})
                rows.append(
                    {
                        "team_slug": tp.team.slug,
                        "team_name": tp.team.name,
                        "provider_id": tp.id,
                        "provider_name": tp.name,
                        "experiment_id": eid,
                        "experiment_name": ename,
                        "archived": bool(archived) if eid is not None else "",
                        "recent_trace_count": u["count"],
                        "last_trace": u["last"].date().isoformat() if u["last"] else "",
                        "window_days": days,
                    }
                )
        return rows

    def _write_csv(self, rows):
        fields = [
            "team_slug",
            "team_name",
            "provider_id",
            "provider_name",
            "experiment_id",
            "experiment_name",
            "archived",
            "recent_trace_count",
            "last_trace",
            "window_days",
        ]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        self.stdout.write(buffer.getvalue(), ending="")

    def _print_report(self, host, days, providers, rows, usage):
        out = self.stdout
        style = self.style

        out.write(style.MIGRATE_HEADING(f"\nLangfuse usage report — host: {host}/  (recent window: {days}d)\n"))

        if not providers:
            out.write(style.WARNING("No Langfuse trace providers configured with this host.\n"))

        # Group rows by team -> provider for a tidy report.
        by_team = defaultdict(lambda: defaultdict(list))
        for row in rows:
            by_team[(row["team_slug"], row["team_name"])][(row["provider_id"], row["provider_name"])].append(row)

        for (slug, tname), providers_rows in sorted(by_team.items()):
            out.write(style.HTTP_INFO(f"\n[{slug}] {tname}"))
            for (pid, pname), exp_rows in providers_rows.items():
                has_experiments = exp_rows[0]["experiment_id"] is not None
                n = len(exp_rows) if has_experiments else 0
                out.write(f'  provider #{pid} "{pname}" — {n} experiment(s)')
                if not has_experiments:
                    out.write(style.WARNING("    (no experiments reference this provider)"))
                    continue
                for r in exp_rows:
                    archived_tag = " [archived]" if r["archived"] else ""
                    last = r["last_trace"] or "—"
                    out.write(
                        f'    exp #{r["experiment_id"]} "{r["experiment_name"]}"{archived_tag}'
                        f" — {r['recent_trace_count']} traces / {days}d (last: {last})"
                    )

        total_recent = sum(r["recent_trace_count"] for r in rows)
        # Recent traces whose experiment is not (or no longer) linked to a matched provider.
        linked_exp_ids = {r["experiment_id"] for r in rows if r["experiment_id"] is not None}
        orphan_count = sum(u["count"] for eid, u in usage.items() if eid not in linked_exp_ids)

        out.write(style.MIGRATE_HEADING("\nSummary"))
        out.write(f"  Configured providers : {len(providers)}")
        out.write(f"  Teams referencing    : {len(by_team)}")
        out.write(f"  Recent traces (linked experiments) : {total_recent}")
        if orphan_count:
            out.write(style.WARNING(f"  Recent traces on this host w/o a linked provider experiment : {orphan_count}"))
        out.write("")
