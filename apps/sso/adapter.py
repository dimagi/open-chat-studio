import logging
from functools import cached_property

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.core.exceptions import PermissionDenied

from apps.teams.invitations import get_invitation_from_request

logger = logging.getLogger("ocs.sso")


class SsoAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        initial_email = request.session.pop("initial_login_email", None)
        validate_email(sociallogin, initial_email)
        if self.request_invitation:
            validate_email(sociallogin, self.request_invitation.email)

    def is_open_for_signup(self, request, sociallogin):
        if self.request_invitation:
            validate_email(sociallogin, self.request_invitation.email)
            return True

        return super().is_open_for_signup(request, sociallogin)

    def get_signup_form_initial_data(self, sociallogin):
        initial = super().get_signup_form_initial_data(sociallogin)
        if not initial["email"] and self.request_invitation:
            initial["email"] = self.request_invitation.email
        return initial

    def authentication_error(self, request, provider_id, error=None, exception=None, extra_context=None):
        # log the error
        logger.error(
            "Authentication error with provider %s: %s",
            provider_id,
            error,
            exc_info=exception,
            extra=extra_context,
        )

    @cached_property
    def request_invitation(self):
        return get_invitation_from_request(self.request)


def validate_email(sociallogin, email):
    if email and sociallogin.user.email and sociallogin.user.email.lower() != email.lower():
        # Check if the provider's email matches the initial email
        logger.warning(
            "The authenticated email %s does not match the one provided %s",
            sociallogin.user.email,
            email,
        )
        raise PermissionDenied("The authenticated email does not match the one provided.")
