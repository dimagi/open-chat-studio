from collections import defaultdict

from apps.assistants.models import OpenAiAssistant
from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.teams.email import collect_team_admin_emails, send_bulk_team_admin_emails
from apps.teams.models import Team

REMOVAL_DATE = "26 August 2026"


class Command(IdempotentCommand):
    help = "Notify team admins about the upcoming removal of OpenAI Assistants"
    migration_name = "notify_openai_assistant_removal_2026_07_23"
    disable_audit = True

    def perform_migration(self, dry_run=False):
        # Only working, non-archived assistants count as "in use" (working_versions_queryset filters both).
        # A list (not a set) so that distinct assistants sharing a name are each counted and listed.
        teams_data = defaultdict(list)
        for assistant in OpenAiAssistant.objects.working_versions_queryset():
            teams_data[assistant.team_id].append(assistant.name)

        if not teams_data:
            self.stdout.write(self.style.SUCCESS("No OpenAI Assistants found"))
            return

        teams_context = {}
        for team_id, names in teams_data.items():
            assistant_names = sorted(names)
            teams_context[team_id] = {
                "assistant_names": assistant_names,
                "assistant_count": len(assistant_names),
                "removal_date": REMOVAL_DATE,
            }

        total_teams = len(teams_context)
        total_assistants = sum(len(names) for names in teams_data.values())

        if self.verbosity > 1:
            teams = {t.id: t for t in Team.objects.filter(id__in=teams_context.keys())}
            self.stdout.write(f"\nFound {total_assistants} OpenAI Assistants affecting {total_teams} teams:")
            for team_id, context in teams_context.items():
                team = teams[team_id]
                admin_emails = collect_team_admin_emails(team)
                self.stdout.write(f"\n  Team: {team.name} (slug: {team.slug})")
                self.stdout.write(f"    Affected assistants ({context['assistant_count']}):")
                for name in context["assistant_names"]:
                    self.stdout.write(f"      - {name}")
                self.stdout.write(f"    Will notify {len(admin_emails)} admin(s): {', '.join(admin_emails)}")
        else:
            self.stdout.write(f"Found {total_assistants} OpenAI Assistants affecting {total_teams} teams")

        if dry_run:
            return f"Would notify {total_teams} team(s)"

        results = send_bulk_team_admin_emails(
            teams_context=teams_context,
            subject_template="Open Chat Studio: OpenAI Assistants removal for {{ team.name }}",
            body_template_path="events/email/openai_assistant_removal.txt",
            fail_silently=False,
        )

        if self.verbosity > 1:
            self.stdout.write("\nEmail results:")
            self.stdout.write(f"  Sent: {results['sent']}")
            self.stdout.write(f"  No admins: {results['no_admins']}")
            self.stdout.write(f"  Failed: {results['failed']}")
            for error in results["errors"]:
                self.stdout.write(self.style.ERROR(f"  Error: {error}"))
        else:
            self.stdout.write(f"Sent {results['sent']} email(s)")
            if results["failed"] > 0:
                self.stdout.write(self.style.WARNING(f"{results['failed']} email(s) failed"))
            for error in results["errors"]:
                self.stdout.write(self.style.ERROR(f"  {error}"))

        return f"Notified {results['sent']} team(s)"
