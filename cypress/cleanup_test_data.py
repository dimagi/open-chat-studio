"""
Script to clean up sample data created for Cypress E2E tests.

This removes all sample chatbots, participants, assistants, files, and other test data
that was created by the bootstrap_data management command.

Run this from the Django project root:
    python cypress/cleanup_test_data.py
"""

import os
import sys

import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gpt_playground.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from field_audit.models import AuditAction  # noqa: E402

from apps.assistants.models import OpenAiAssistant  # noqa: E402
from apps.chat.models import Chat  # noqa: E402
from apps.documents.models import Collection  # noqa: E402
from apps.experiments.models import Experiment, Participant  # noqa: E402
from apps.files.models import File  # noqa: E402
from apps.pipelines.models import Pipeline  # noqa: E402
from apps.service_providers.models import LlmProvider  # noqa: E402
from apps.teams.models import Team  # noqa: E402


def cleanup_test_data():
    from django.conf import settings

    if not settings.DEBUG:
        raise Exception("This script can only be used run in local test environments mode.")

    User = get_user_model()

    # Configuration - must match cypress.env.json
    email = "test@example.com"
    team_slug = "test-team"

    print("Cleaning up Cypress test data...")
    print("=" * 50)

    # Get test user and team
    try:
        user = User.objects.get(email=email)
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

    # Define models to delete in order (with optional special handling)
    models_to_delete = [
        {"model": Experiment, "name": "Chatbots (Experiments)", "plural": "experiment(s)", "audit": True},
        {"model": Pipeline, "name": "Pipelines", "plural": "pipeline(s)", "audit": False},
        {"model": OpenAiAssistant, "name": "Assistants", "plural": "assistant(s)", "audit": True},
        {
            "model": Participant,
            "name": "Participants",
            "plural": "participant(s) and their sessions/chats",
            "audit": False,
        },
        {"model": Chat, "name": "Remaining Chats", "plural": "chat(s)", "audit": False},
        {"model": Collection, "name": "Collections", "plural": "collection(s)", "audit": True},
        {"model": LlmProvider, "name": "LLM Providers", "plural": "LLM provider(s)", "audit": True},
    ]

    # Delete each model type
    for config in models_to_delete:
        print(f"\n--- Deleting {config['name']} ---")
        queryset = config["model"].objects.filter(team=team)
        count = queryset.count()
        if count > 0:
            if config["audit"]:
                queryset.delete(audit_action=AuditAction.AUDIT)
            else:
                queryset.delete()
            print(f"  ✓ Deleted {count} {config['plural']}")
        else:
            print(f"  ⚠ No {config['plural'].split()[0].lower()} to delete")

    # Files need special handling for file deletion
    print("\n--- Deleting Files ---")
    files = File.objects.filter(team=team)
    count = files.count()
    if count > 0:
        for file_obj in files:
            if file_obj.file:
                file_obj.file.delete(save=False)
        files.delete()
        print(f"  ✓ Deleted {count} file(s)")
    else:
        print("  ⚠ No files to delete")

    print("\n--- Deleting Team ---")
    team.delete()
    print(f"  ✓ Deleted team: {team_slug}")

    print("\n--- Deleting User ---")
    user.delete()
    print(f"  ✓ Deleted user: {email}")

    print("\n" + "=" * 50)
    print("✅ Complete cleanup finished!")
    print("\nAll test data, team, and user have been deleted.")
    print("To recreate the test setup, run:")
    print("  python manage.py bootstrap_data")


if __name__ == "__main__":
    try:
        cleanup_test_data()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
