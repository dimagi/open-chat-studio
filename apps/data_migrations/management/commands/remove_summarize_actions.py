from collections import defaultdict

from apps.data_migrations.management.commands.base import IdempotentCommand
from apps.events.models import EventAction, ScheduledMessage, StaticTrigger, TimeoutTrigger
from apps.teams.email import collect_team_admin_emails, send_bulk_team_admin_emails


class Command(IdempotentCommand):
    help = "Remove all summarize event actions and notify team admins"
    migration_name = "remove_summarize_actions_2026_01_20"
    disable_audit = True

    def perform_migration(self, dry_run=False):
        # Collect affected data
        summarize_actions = EventAction.objects.filter(action_type="summarize").select_related()

        if not summarize_actions.exists():
            self.stdout.write(self.style.SUCCESS("No summarize actions found"))
            return

        # Build affected experiments by team
        teams_data = defaultdict(lambda: {"experiments": set()})

        # Process static triggers
        for trigger in StaticTrigger.objects.filter(action__action_type="summarize").select_related(
            "experiment__team", "action"
        ):
            teams_data[trigger.experiment.team_id]["experiments"].add(trigger.experiment.name)

        # Process timeout triggers
        for trigger in TimeoutTrigger.objects.filter(action__action_type="summarize").select_related(
            "experiment__team", "action"
        ):
            teams_data[trigger.experiment.team_id]["experiments"].add(trigger.experiment.name)

        # Process scheduled messages
        for msg in ScheduledMessage.objects.filter(action__action_type="summarize").select_related(
            "experiment__team", "action"
        ):
            teams_data[msg.experiment.team_id]["experiments"].add(msg.experiment.name)

        # Convert to email context format
        teams_context = {}
        for team_id, data in teams_data.items():
            experiments = sorted(data["experiments"])
            teams_context[team_id] = {
                "chatbot_names": experiments,
                "chatbot_count": len(experiments),
            }

        # Show what will be affected (load teams for display)
        from apps.teams.models import Team

        total_actions = summarize_actions.count()
        total_teams = len(teams_context)
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
        self.stdout.write("\nEmail results:")
        self.stdout.write(f"  Sent: {results['sent']}")
        self.stdout.write(f"  No admins: {results['no_admins']}")
        self.stdout.write(f"  Failed: {results['failed']}")
        for error in results["errors"]:
            self.stdout.write(self.style.ERROR(f"  Error: {error}"))

        # Delete the actions (triggers will cascade)
        deleted_count = summarize_actions.count()
        summarize_actions.delete()

        self.stdout.write(self.style.SUCCESS(f"\nRemoved {deleted_count} summarize actions"))

        return f"Removed {deleted_count} actions, notified {results['sent']} teams"
