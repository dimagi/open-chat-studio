"""
Script to create a test user and team for Cypress E2E tests.

Run this from the Django project root:
    python cypress/create_test_user.py
"""

import os
import sys

import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gpt_playground.settings")
django.setup()

# NOTE: needs to be below this code but does throw lint error
from django.contrib.auth import get_user_model  # noqa: E402

from apps.teams import backends  # noqa: E402
from apps.teams.models import Membership, Team  # noqa: E402


def create_test_user():
    User = get_user_model()

    email = "test@example.com"
    password = "testpassword"
    team_name = "Test Team"
    team_slug = "test-team"

    print("Creating Cypress test user and team...")
    print("=" * 50)
    try:
        backends.create_default_groups()
        print("✓ Default groups initialized")
    except Exception as e:
        print(f"⚠ Could not create default groups: {e}")

    user, created = User.objects.get_or_create(username=email, defaults={"email": email})

    if created:
        user.set_password(password)
        user.save()
        print(f"✓ Created user: {email}")
    else:
        print(f"⚠ User already exists: {email}")
        # Update password in case it changed
        user.set_password(password)
        user.save()
        print(f"✓ Updated password for: {email}")

    team, created = Team.objects.get_or_create(slug=team_slug, defaults={"name": team_name})

    if created:
        print(f"✓ Created team: {team_slug}")
    else:
        print(f"⚠ Team already exists: {team_slug}")
    membership = Membership.objects.filter(user=user, team=team).first()

    if not membership:
        membership = backends.make_user_team_owner(team, user)
        print("✓ Added user to team as owner (full permissions)")
    else:
        print("⚠ User already member of team")
        membership.groups.set(backends.get_team_owner_groups())
        print("✓ Updated user to have owner permissions")

    print("=" * 50)
    print("\nSetup complete! Update your cypress.env.json with:")
    print()
    print("{")
    print(f'  "TEAM_SLUG": "{team_slug}",')
    print(f'  "TEST_USER": "{email}",')
    print(f'  "TEST_PASSWORD": "{password}"')
    print("}")
    print()
    print("To seed test data (chatbots, files, participants, etc.), run:")
    print("  python cypress/seed_test_data.py")
    print()
    print("Once seeded, you can run Cypress tests:")
    print("  npx cypress open")
    print("  npx cypress run")


if __name__ == "__main__":
    try:
        create_test_user()
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
