from collections import defaultdict

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.events.models import EventAction, ScheduledMessage, StaticTrigger, TimeoutTrigger
from apps.teams.email import collect_team_admin_emails, send_bulk_team_admin_emails


class Command(IdempotentCommand):
    help = "Remove all summarize event actions and notify team admins"
    migration_name = "remove_summarize_actions_2026_01_20"
    disable_audit = True

    def perform_migration(self, dry_run=False):
        # Collect all affected data (all versions)
        summarize_actions = EventAction.objects.filter(action_type="summarize").select_related()

        if not summarize_actions.exists():
            self.stdout.write(self.style.SUCCESS("No summarize actions found"))
            return

        # Build affected experiments by team (only working versions for email notifications)
        teams_data = defaultdict(lambda: {"experiments": set()})
        for trigger in StaticTrigger.objects.filter(
            action__action_type="summarize", experiment__working_version__isnull=True
        ).select_related("experiment__team", "action"):
            teams_data[trigger.experiment.team_id]["experiments"].add(trigger.experiment.name)

        for trigger in TimeoutTrigger.objects.filter(
            action__action_type="summarize", experiment__working_version__isnull=True
        ).select_related("experiment__team", "action"):
            teams_data[trigger.experiment.team_id]["experiments"].add(trigger.experiment.name)

        for msg in ScheduledMessage.objects.filter(
            action__action_type="summarize", experiment__working_version__isnull=True
        ).select_related("experiment__team", "action"):
            teams_data[msg.experiment.team_id]["experiments"].add(msg.experiment.name)

        # Convert to email context format
        teams_context = {}
        for team_id, data in teams_data.items():
            experiments = sorted(data["experiments"])
            teams_context[team_id] = {
                "chatbot_names": experiments,
                "chatbot_count": len(experiments),
            }

        # Show summary
        total_actions = summarize_actions.count()
        total_teams = len(teams_context)

        if self.verbosity > 1:
            # Verbose output: show details for each team
            from apps.teams.models import Team

            teams = {t.id: t for t in Team.objects.filter(id__in=teams_context.keys())}

            self.stdout.write(f"\nFound {total_actions} summarize actions affecting {total_teams} teams:")
            for team_id, context in teams_context.items():
                team = teams[team_id]
                admin_emails = collect_team_admin_emails(team)
                self.stdout.write(f"\n  Team: {team.name} (slug: {team.slug})")
                self.stdout.write(f"    Affected chatbots ({context['chatbot_count']}):")
                for exp_name in context["chatbot_names"]:
                    self.stdout.write(f"      - {exp_name}")
                self.stdout.write(f"    Will notify {len(admin_emails)} admin(s): {', '.join(admin_emails)}")
        else:
            # Normal output: just summary
            self.stdout.write(f"Found {total_actions} summarize actions affecting {total_teams} teams")

        if dry_run:
            return f"Would remove {total_actions} summarize actions"

        # Send emails to team admins
        results = send_bulk_team_admin_emails(
            teams_context=teams_context,
            subject_template="Open Chat Studio: Summarize feature removed from {{ team.name }}",
            body_template_name="events/email/summarize_removal",
            fail_silently=False,
        )

        # Report email results
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
            if results["errors"]:
                for error in results["errors"]:
                    self.stdout.write(self.style.ERROR(f"  {error}"))

        # Delete triggers and scheduled messages (actions cascade)
        static_count = StaticTrigger.objects.filter(action__action_type="summarize").count()
        timeout_count = TimeoutTrigger.objects.filter(action__action_type="summarize").count()
        scheduled_count = ScheduledMessage.objects.filter(action__action_type="summarize").count()

        StaticTrigger.objects.filter(action__action_type="summarize").delete()
        TimeoutTrigger.objects.filter(action__action_type="summarize").delete()
        ScheduledMessage.objects.filter(action__action_type="summarize").delete()

        # Delete any remaining orphaned actions
        remaining_actions = EventAction.objects.filter(action_type="summarize").count()
        if remaining_actions > 0:
            EventAction.objects.filter(action_type="summarize").delete()

        total_deleted = static_count + timeout_count + scheduled_count

        if self.verbosity > 1:
            self.stdout.write(self.style.SUCCESS(f"\nRemoved {total_deleted} total items:"))
            self.stdout.write(f"  Static triggers: {static_count}")
            self.stdout.write(f"  Timeout triggers: {timeout_count}")
            self.stdout.write(f"  Scheduled messages: {scheduled_count}")
            if remaining_actions > 0:
                self.stdout.write(f"  Orphaned actions: {remaining_actions}")
        else:
            self.stdout.write(self.style.SUCCESS(f"Removed {total_deleted} items"))

        return f"Removed {total_deleted} items, notified {results['sent']} teams"
