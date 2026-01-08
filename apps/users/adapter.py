from allauth.account import app_settings
from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.utils import user_email, user_field
from allauth.mfa.adapter import DefaultMFAAdapter
from django.conf import settings


class EmailAsUsernameAdapter(DefaultAccountAdapter):
    """
    Adapter that always sets the username equal to the user's email address.
    """

    def populate_username(self, request, user):
        # override the username population to always use the email
        user_field(user, app_settings.USER_MODEL_USERNAME_FIELD, user_email(user))


class AccountAdapter(EmailAsUsernameAdapter):
    pass


class MfaAdapter(DefaultMFAAdapter):
    """
    Custom MFA adapter for Open Chat Studio.
    Handles encryption of TOTP secrets if CRYPTOGRAPHY_SALT is configured.
    """

    def encrypt(self, text: str) -> str:
        """Encrypt TOTP secrets using Django cryptography if configured."""
        if not settings.CRYPTOGRAPHY_SALT:
            return text

        from django_cryptography.core import encrypt as django_encrypt

        return django_encrypt(text)

    def decrypt(self, encrypted_text: str) -> str:
        """Decrypt TOTP secrets."""
        if not settings.CRYPTOGRAPHY_SALT:
            return encrypted_text

        from django_cryptography.core import decrypt as django_decrypt

        return django_decrypt(encrypted_text)
