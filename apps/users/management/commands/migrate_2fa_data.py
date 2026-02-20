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
            return cursor.fetchone()[0]  # ty: ignore[not-subscriptable]

    def _get_totp_devices(self):
        """Get TOTP devices using raw SQL."""
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    d.id,
                    d.user_id,
                    d.key
                FROM otp_totp_totpdevice d
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

    def handle(self, **options):
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
        authenticators_to_create = []

        # Import User model
        from apps.users.models import CustomUser

        # De-duplicate to at most one confirmed device per user (choose first)
        devices_by_user_id = {}
        for d in totp_devices:
            devices_by_user_id.setdefault(d["user_id"], d)

        for user_id, device in devices_by_user_id.items():
            try:
                user = CustomUser.objects.get(id=user_id)
            except CustomUser.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"User id={user_id} not found, skipping"))
                continue

            existing_types = set(Authenticator.objects.filter(user=user).values_list("type", flat=True))

            # Convert hex key to base32
            try:
                hex_key = device["key"]
                # Remove any spaces and convert to bytes
                key_bytes = binascii.unhexlify(hex_key.replace(" ", ""))
                # Convert to base32
                base32_secret = base64.b32encode(key_bytes).decode("utf-8")
            except (ValueError, binascii.Error) as e:
                self.stdout.write(self.style.ERROR(f"Failed to convert TOTP key for user id={user_id}: {e}"))
                continue

            if Authenticator.Type.TOTP not in existing_types:
                encrypted_secret = adapter.encrypt(base32_secret)
                authenticators_to_create.append(
                    Authenticator(user=user, type=Authenticator.Type.TOTP, data={"secret": encrypted_secret})
                )

            # Get recovery codes from StaticDevice using raw SQL
            static_tokens = self._get_static_tokens(user_id)

            if static_tokens:
                if Authenticator.Type.RECOVERY_CODES not in existing_types:
                    unused_codes = [adapter.encrypt(token) for token in static_tokens]
                    authenticators_to_create.append(
                        Authenticator(
                            user=user,
                            type=Authenticator.Type.RECOVERY_CODES,
                            data={"migrated_codes": unused_codes},
                        )
                    )

            self.stdout.write(self.style.SUCCESS(f"Prepared migration for user id={user_id}"))
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

        self.stdout.write(
            self.style.SUCCESS("\nMigration complete!\n2FA functionality has been restored for migrated users.")
        )
