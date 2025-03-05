import logging

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

logger = logging.getLogger("ocs.sso")


class SsoAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        # Retrieve the initial email from the session
        initial_email = request.session.pop("initial_login_email", None)
        if initial_email and sociallogin.user.email:
            # Check if the provider's email matches the initial email
            if sociallogin.user.email.lower() != initial_email.lower():
                logger.warning(
                    "The authenticated email %s does not match the one provided %s",
                    sociallogin.user.email,
                    initial_email,
                )
                # raise PermissionDenied("The authenticated email does not match the one provided.")

    def authentication_error(self, request, provider_id, error=None, exception=None, extra_context=None):
        # log the error
        logger.error(
            "Authentication error with provider %s: %s",
            provider_id,
            error,
            exc_info=exception,
            extra=extra_context,
        )
