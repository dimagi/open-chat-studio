"""Send a password-reset email to a synced user so they can set a password on the target server
(passwords are hashed and never migrated)."""

from allauth.account.forms import ResetPasswordForm


def send_password_reset_email(user):
    if not user.email:
        return
    form = ResetPasswordForm(data={"email": user.email})
    if form.is_valid():
        form.save(None)
