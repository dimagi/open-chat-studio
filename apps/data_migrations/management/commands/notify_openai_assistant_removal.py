from collections import defaultdict

from django.core.management.base import CommandError

from apps.assistants.models import OpenAiAssistant
from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.teams.email import collect_team_admin_emails, send_bulk_team_admin_emails
from apps.teams.models import Team

REMOVAL_DATE = "26 August 2026"


class Command(IdempotentCommand):
    help = "Notify team admins about the upcoming removal of OpenAI Assistants"
    migration_name = "notify_openai_assistant_removal_2026_07_23"
    disable_audit = True

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--team-ids",
            nargs="+",
            type=int,
            default=None,
            help="Only notify these team IDs (default: all teams with a working assistant)",
        )

    def handle(self, *args, **options):
        self.team_ids = options.get("team_ids")
        super().handle(*args, **options)

    def perform_migration(self, dry_run=False):
        teams_context = self._build_teams_context()
        if not teams_context:
            self.stdout.write(self.style.SUCCESS("No OpenAI Assistants found"))
            return

        total_teams = len(teams_context)
        total_assistants = sum(context["assistant_count"] for context in teams_context.values())
        self._report_preview(teams_context, total_teams, total_assistants)

        if dry_run:
            return f"Would notify {total_teams} team(s)"

        results = send_bulk_team_admin_emails(
            teams_context=teams_context,
            subject_template="Open Chat Studio: OpenAI Assistants removal for {{ team.name }}",
            body_template_path="events/email/openai_assistant_removal.txt",
            fail_silently=False,
        )
        self._report_results(results)

        if results["failed"]:
            # Fail loudly so IdempotentCommand does not mark this run applied; otherwise the
            # teams whose emails failed would never be notified. A re-run resends to all teams.
            raise CommandError(f"{results['failed']} notification email(s) failed")

        return f"Notified {results['sent']} team(s)"

    def _build_teams_context(self):
        # Only working, non-archived assistants count as "in use" (working_versions_queryset filters both).
        # A list (not a set) so that distinct assistants sharing a name are each counted and listed.
        teams_data = defaultdict(list)
        assistants = OpenAiAssistant.objects.working_versions_queryset()
        if self.team_ids:
            assistants = assistants.filter(team_id__in=self.team_ids)
        for assistant in assistants:
            teams_data[assistant.team_id].append(assistant.name)

        return {
            team_id: {
                "assistant_names": sorted(names),
                "assistant_count": len(names),
                "removal_date": REMOVAL_DATE,
            }
            for team_id, names in teams_data.items()
        }

    def _report_preview(self, teams_context, total_teams, total_assistants):
        if self.verbosity <= 1:
            self.stdout.write(f"Found {total_assistants} OpenAI Assistants affecting {total_teams} teams")
            return

        teams = {t.id: t for t in Team.objects.filter(id__in=teams_context.keys())}
        self.stdout.write(f"\nFound {total_assistants} OpenAI Assistants affecting {total_teams} teams:")
        for team_id, context in teams_context.items():
            team = teams[team_id]
            admin_count = len(collect_team_admin_emails(team))
            self.stdout.write(f"\n  Team: {team.name} (slug: {team.slug})")
            self.stdout.write(f"    Affected assistants ({context['assistant_count']}):")
            for name in context["assistant_names"]:
                self.stdout.write(f"      - {name}")
            self.stdout.write(f"    Will notify {admin_count} admin(s)")

    def _report_results(self, results):
        if self.verbosity > 1:
            self.stdout.write("\nEmail results:")
            self.stdout.write(f"  Sent: {results['sent']}")
            self.stdout.write(f"  No admins: {results['no_admins']}")
            self.stdout.write(f"  Failed: {results['failed']}")
        else:
            self.stdout.write(f"Sent {results['sent']} email(s)")
            if results["failed"] > 0:
                self.stdout.write(self.style.WARNING(f"{results['failed']} email(s) failed"))
        for error in results["errors"]:
            self.stdout.write(self.style.ERROR(f"  {error}"))
