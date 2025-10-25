"""
Django management command to create a Cypress test user.

Usage:
    python manage.py create_cypress_test_user
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.teams import backends
from apps.teams.models import Membership, Team


class Command(BaseCommand):
    help = "Creates a test user and team for Cypress E2E tests"

    def handle(self, *args, **options):
        User = get_user_model()

        # Configuration
        username = "testuser"
        email = "test@example.com"
        password = "testpassword"
        team_name = "Test Team"
        team_slug = "test-team"

        self.stdout.write("Creating Cypress test user and team...")
        self.stdout.write("=" * 50)

        # Ensure default groups exist
        try:
            backends.create_default_groups()
            self.stdout.write(self.style.SUCCESS("✓ Default groups initialized"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"⚠ Could not create default groups: {e}"))

        # Create or get user
        user, created = User.objects.get_or_create(username=username, defaults={"email": email})

        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"✓ Created user: {email}"))
        else:
            self.stdout.write(self.style.WARNING(f"⚠ User already exists: {email}"))
            # Update password in case it changed
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"✓ Updated password for: {email}"))

        # Create or get team
        team, created = Team.objects.get_or_create(slug=team_slug, defaults={"name": team_name})

        if created:
            self.stdout.write(self.style.SUCCESS(f"✓ Created team: {team_slug}"))
        else:
            self.stdout.write(self.style.WARNING(f"⚠ Team already exists: {team_slug}"))

        # Add user to team with owner permissions
        membership = Membership.objects.filter(user=user, team=team).first()

        if not membership:
            # Create new membership with full permissions
            membership = backends.make_user_team_owner(team, user)
            self.stdout.write(self.style.SUCCESS("✓ Added user to team as owner (full permissions)"))
        else:
            self.stdout.write(self.style.WARNING("⚠ User already member of team"))
            # Update to ensure they have owner permissions
            membership.groups.set(backends.get_team_owner_groups())
            self.stdout.write(self.style.SUCCESS("✓ Updated user to have owner permissions"))

        self.stdout.write("=" * 50)
        self.stdout.write("\nSetup complete! Update your cypress.env.json with:")
        self.stdout.write("")
        self.stdout.write("{")
        self.stdout.write(f'  "TEAM_SLUG": "{team_slug}",')
        self.stdout.write(f'  "TEST_USER": "{email}",')
        self.stdout.write(f'  "TEST_PASSWORD": "{password}"')
        self.stdout.write("}")
        self.stdout.write("")
        self.stdout.write("You can now run Cypress tests:")
        self.stdout.write("  npx cypress open")
        self.stdout.write("  npx cypress run")
