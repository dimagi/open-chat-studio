"""
Management command to migrate 2FA data from django-allauth-2fa to allauth.mfa.
Uses RAW SQL to read from old otp_* tables (doesn't require django-otp models).
Based on: https://docs.allauth.org/en/dev/mfa/django-allauth-2fa.html
"""

import base64
import binascii

from allauth.mfa.adapter import get_adapter
from allauth.mfa.models import Authenticator
from django.core.management.base import BaseCommand
from django.db import connection, transaction


class Command(BaseCommand):
    help = "Migrate 2FA data from django-otp tables to allauth.mfa"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run migration in dry-run mode (no database changes)",
        )

    def _check_old_tables_exist(self):
        """Check if old django-otp tables exist in database."""
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'otp_totp_totpdevice'
                )
            """)
            return cursor.fetchone()[0]

    def _get_totp_devices(self):
        """Get TOTP devices using raw SQL."""
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    d.id,
                    d.user_id,
                    d.key,
                    u.email
                FROM otp_totp_totpdevice d
                JOIN users_customuser u ON u.id = d.user_id
                WHERE d.confirmed = true
            """)
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]

    def _get_static_tokens(self, user_id):
        """Get static tokens (recovery codes) for a user using raw SQL."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT t.token
                FROM otp_static_statictoken t
                JOIN otp_static_staticdevice d ON d.id = t.device_id
                WHERE d.user_id = %s AND d.confirmed = true
            """,
                [user_id],
            )
            return [row[0] for row in cursor.fetchall()]

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No changes will be made"))

        # Check if old tables exist
        if not self._check_old_tables_exist():
            self.stdout.write(
                self.style.ERROR(
                    "Old django-otp tables not found. Migration may have already been run, or tables were deleted."
                )
            )
            return

        adapter = get_adapter()

        # Get all confirmed TOTP devices using raw SQL
        totp_devices = self._get_totp_devices()

        if not totp_devices:
            self.stdout.write(self.style.SUCCESS("No TOTP devices to migrate."))
            return

        self.stdout.write(f"Found {len(totp_devices)} TOTP devices to migrate")

        migrated_count = 0
        skipped_count = 0
        authenticators_to_create = []

        # Import User model
        from apps.users.models import CustomUser

        for device in totp_devices:
            user_id = device["user_id"]
            user_email = device["email"]

            try:
                user = CustomUser.objects.get(id=user_id)
            except CustomUser.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"User {user_email} not found, skipping"))
                continue

            # Check if user already has MFA configured (skip if already migrated)
            if Authenticator.objects.filter(user=user, type=Authenticator.Type.TOTP).exists():
                self.stdout.write(
                    self.style.WARNING(f"Skipping user {user_email} - already has allauth.mfa configured")
                )
                skipped_count += 1
                continue

            # Convert hex key to base32
            try:
                hex_key = device["key"]
                # Remove any spaces and convert to bytes
                key_bytes = binascii.unhexlify(hex_key.replace(" ", ""))
                # Convert to base32
                base32_secret = base64.b32encode(key_bytes).decode("utf-8")
            except (ValueError, binascii.Error) as e:
                self.stdout.write(self.style.ERROR(f"Failed to convert TOTP key for user {user_email}: {e}"))
                continue

            # Encrypt the secret
            encrypted_secret = adapter.encrypt(base32_secret)

            # Create TOTP authenticator
            totp_authenticator = Authenticator(
                user=user, type=Authenticator.Type.TOTP, data={"secret": encrypted_secret}
            )
            authenticators_to_create.append(totp_authenticator)

            # Get recovery codes from StaticDevice using raw SQL
            static_tokens = self._get_static_tokens(user_id)

            if static_tokens:
                # Encrypt unused recovery codes
                unused_codes = [adapter.encrypt(token) for token in static_tokens]

                # Create recovery codes authenticator
                recovery_authenticator = Authenticator(
                    user=user, type=Authenticator.Type.RECOVERY_CODES, data={"unused_codes": unused_codes}
                )
                authenticators_to_create.append(recovery_authenticator)

                self.stdout.write(f"  Migrating {len(unused_codes)} recovery codes for {user_email}")

            self.stdout.write(self.style.SUCCESS(f"Prepared migration for user: {user_email}"))
            migrated_count += 1

        # Bulk create all authenticators
        if not dry_run and authenticators_to_create:
            with transaction.atomic():
                Authenticator.objects.bulk_create(authenticators_to_create)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"\nSuccessfully migrated {migrated_count} users "
                        f"({len(authenticators_to_create)} authenticators created)"
                    )
                )
        elif dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"\nDRY RUN: Would have migrated {migrated_count} users "
                    f"({len(authenticators_to_create)} authenticators)"
                )
            )

        if skipped_count > 0:
            self.stdout.write(f"Skipped {skipped_count} users (already migrated)")

        self.stdout.write(
            self.style.SUCCESS("\nMigration complete!\n2FA functionality has been restored for migrated users.")
        )
