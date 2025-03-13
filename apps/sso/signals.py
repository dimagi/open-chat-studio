from allauth.account.signals import user_logged_in
from django.dispatch import receiver

from apps.sso.models import SsoSession


@receiver(user_logged_in)
def user_logged_in_signal(request, user, **kwargs):
    """
    This signal is used to create a SsoSession object when a user logs in. This is used to link
    the SSO session to the Django session so that we can log out the Django session when the
    SSO session is logged out.
    """
    if sso_session_id := request.GET.get("session_state"):
        SsoSession.objects.update_or_create(
            id=sso_session_id, defaults={"django_session_id": request.session.session_key, "user": user}
        )
