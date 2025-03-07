from allauth.account import app_settings
from allauth.account.utils import user_email, user_field
from allauth_2fa.adapter import OTPAdapter as AllAuthOtpAdapter


class EmailAsUsernameAdapter(AllAuthOtpAdapter):
    """
    Adapter that always sets the username equal to the user's email address.
    """

    def populate_username(self, request, user):
        # override the username population to always use the email
        user_field(user, app_settings.USER_MODEL_USERNAME_FIELD, user_email(user))


class AccountAdapter(EmailAsUsernameAdapter):
    pass
