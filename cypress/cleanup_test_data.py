"""
Script to clean up sample data created for Cypress E2E tests.

This removes all sample chatbots, participants, assistants, files, and other test data
that was created by the seed_test_data.py script.

Run this from the Django project root:
    python cypress/cleanup_test_data.py
"""

import os
import sys

import django
from django.contrib.auth import get_user_model
from field_audit.models import AuditAction

from apps.assistants.models import OpenAiAssistant
from apps.chat.models import Chat
from apps.experiments.models import Experiment, Participant
from apps.files.models import File
from apps.pipelines.models import Pipeline
from apps.service_providers.models import LlmProvider
from apps.teams.models import Team

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gpt_playground.settings")
django.setup()


def cleanup_test_data():
    User = get_user_model()

    # Configuration - must match cypress.env.json
    email = "test@example.com"
    team_slug = "test-team"

    print("Cleaning up Cypress test data...")
    print("=" * 50)

    # Get test user and team
    try:
        User.objects.get(email=email)
        print(f"✓ Found user: {email}")
    except User.DoesNotExist:
        print(f"❌ User not found: {email}")
        print("   No cleanup needed!")
        sys.exit(0)

    try:
        team = Team.objects.get(slug=team_slug)
        print(f"✓ Found team: {team_slug}")
    except Team.DoesNotExist:
        print(f"❌ Team not found: {team_slug}")
        print("   No cleanup needed!")
        sys.exit(0)

    # Delete experiments (chatbots)
    print("\n--- Deleting Chatbots (Experiments) ---")
    experiments = Experiment.objects.filter(team=team)
    count = experiments.count()
    if count > 0:
        experiments.delete(audit_action=AuditAction.AUDIT)
        print(f"  ✓ Deleted {count} experiment(s)")
    else:
        print("  ⚠ No experiments to delete")

    # Delete pipelines
    print("\n--- Deleting Pipelines ---")
    pipelines = Pipeline.objects.filter(team=team)
    count = pipelines.count()
    if count > 0:
        pipelines.delete()
        print(f"  ✓ Deleted {count} pipeline(s)")
    else:
        print("  ⚠ No pipelines to delete")

    # Delete assistants
    print("\n--- Deleting Assistants ---")
    assistants = OpenAiAssistant.objects.filter(team=team)
    count = assistants.count()
    if count > 0:
        assistants.delete(audit_action=AuditAction.AUDIT)
        print(f"  ✓ Deleted {count} assistant(s)")
    else:
        print("  ⚠ No assistants to delete")

    # Delete participants (and their sessions/chats)
    print("\n--- Deleting Participants ---")
    participants = Participant.objects.filter(team=team)
    count = participants.count()
    if count > 0:
        participants.delete()
        print(f"  ✓ Deleted {count} participant(s) and their sessions/chats")
    else:
        print("  ⚠ No participants to delete")

    # Delete any remaining orphaned chats
    print("\n--- Deleting Orphaned Chats ---")
    chats = Chat.objects.filter(team=team)
    count = chats.count()
    if count > 0:
        chats.delete()
        print(f"  ✓ Deleted {count} orphaned chat(s)")
    else:
        print("  ⚠ No orphaned chats to delete")

    # Delete files
    print("\n--- Deleting Files ---")
    files = File.objects.filter(team=team)
    count = files.count()
    if count > 0:
        # Delete the actual files from storage
        for file_obj in files:
            if file_obj.file:
                file_obj.file.delete(save=False)
        files.delete()
        print(f"  ✓ Deleted {count} file(s)")
    else:
        print("  ⚠ No files to delete")

    # Delete LLM providers
    print("\n--- Deleting LLM Providers ---")
    llm_providers = LlmProvider.objects.filter(team=team)
    count = llm_providers.count()
    if count > 0:
        llm_providers.delete(audit_action=AuditAction.AUDIT)
        print(f"  ✓ Deleted {count} LLM provider(s)")
    else:
        print("  ⚠ No LLM providers to delete")

    print("\n" + "=" * 50)
    print("✅ Test data cleanup complete!")
    print("\nNote: The test user and team were NOT deleted.")
    print("To delete those as well, run:")
    print(
        f"  python manage.py shell -c \"from django.contrib.auth import get_user_model; \
        from apps.teams.models import Team; Team.objects.get(slug='{team_slug}').delete(); \
        get_user_model().objects.get(email='{email}').delete()\""
    )


if __name__ == "__main__":
    try:
        cleanup_test_data()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
