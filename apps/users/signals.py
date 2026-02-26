from allauth.account.signals import email_confirmed, user_signed_up
from django.conf import settings
from django.core.mail import mail_admins
from django.dispatch import receiver


@receiver(user_signed_up)
def handle_sign_up(request, user, **kwargs):
    # customize this function to do custom logic on sign up, e.g. send a welcome email.
    # This example notifies the admins, in case you want to keep track of sign ups.
    _notify_admins_of_signup(user)


@receiver(email_confirmed)
def update_user_email(sender, request, email_address, **kwargs):
    """
    When an email address is confirmed make it the primary email.
    """
    # This also sets user.email to the new email address.
    # hat tip: https://stackoverflow.com/a/29661871/8207
    email_address.set_as_primary()


def _notify_admins_of_signup(user):
    mail_admins(
        f"Yowsers, someone signed up for {settings.PROJECT_METADATA['NAME']}!",
        f"Email: {user.email}",
        fail_silently=True,
    )
