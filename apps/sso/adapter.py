from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.core.exceptions import PermissionDenied


class SsoAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        # Retrieve the initial email from the session
        initial_email = request.session.pop("initial_login_email", None)

        if initial_email:
            provider_email = sociallogin.user.email

            # Check if the provider's email matches the initial email
            if provider_email.lower() != initial_email.lower():
                raise PermissionDenied("The authenticated email does not match the one provided.")
