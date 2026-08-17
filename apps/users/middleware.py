from urllib.parse import urlparse

from allauth.account.models import EmailAddress
from allauth.mfa import app_settings as mfa_settings
from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin
from django.utils.translation import gettext_lazy as _
from django_htmx.http import HttpResponseClientRedirect


def _path_of(url: str | None) -> str:
    return urlparse(url).path if url else ""


class RequireMfaForStaffMiddleware(MiddlewareMixin):
    """Confine staff and superusers without MFA to the MFA setup flow.

    allauth's ``mfa`` app ships no enforcement hook, so this follows the ``BaseRequire2FAMiddleware``
    pattern documented by django-allauth-2fa:
    https://django-allauth-2fa.readthedocs.io/en/latest/advanced/

    Only the session-authenticated web UI is gated. The API and webhook surfaces authenticate per
    request (API key, OAuth token, platform signature) and can't act on a redirect, so a redirect
    there would break integrations rather than prompt anyone to enrol.
    """

    MESSAGE = _("Two-factor authentication is required for staff accounts. Please set it up to continue.")

    VERIFY_EMAIL_MESSAGE = _(
        "Two-factor authentication is required for staff accounts, and it can only be set up once "
        "your email address is verified."
    )

    ALLOWED_VIEW_NAMES = frozenset({"set_language"})

    #: ``/accounts/`` is where config.urls mounts allauth (plus the OCS SSO views): the MFA setup
    #: flow itself, login/logout, password and email management, and the social provider
    #: login/callback URLs. All of it stays open, for two reasons. Gating any of it breaks the very
    #: flows a user needs to enrol -- an SSO login can't complete if the provider callback is
    #: redirected away, and the IdP's front-channel logout would leave the session alive. It also
    #: keeps the gate loop-free: ``/accounts/login/`` bounces an authenticated user to
    #: ``LOGIN_REDIRECT_URL``, which the gate sends back here, so any gated URL under ``/accounts/``
    #: is a redirect cycle waiting to happen.
    #:
    #: The remaining prefixes are surfaces that don't use session auth (they authenticate per
    #: request with an API key, OAuth token, or platform signature), where redirecting the caller
    #: achieves nothing -- plus ``/__reload__/``, whose event stream the dev server's browser-reload
    #: worker reconnects to and reloads the page over if it is ever redirected.
    EXEMPT_PATH_PREFIXES = (
        "/accounts/",
        "/api/",
        "/channels/",
        "/anymail/",
        "/celery-progress/",
        "/tz_detect/",
        "/__reload__/",
    )

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not self._must_enrol(request):
            return None
        return self._gate(request)

    def _must_enrol(self, request) -> bool:
        if not settings.REQUIRE_MFA_FOR_STAFF:
            return False

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False

        if not (user.is_staff or user.is_superuser):
            return False

        return not (is_mfa_enabled(user) or self._is_exempt(request))

    def _gate(self, request):
        if request.htmx and self._is_exempt_path(_path_of(request.htmx.current_url)):
            # This is a background request fired by a page the gate already lets through -- the
            # banner poll on the setup page, say. Telling htmx to navigate would send the browser
            # to the page it is already on, which fires the request again: an endless reload loop.
            # 204 leaves the fragment unswapped, and unlike a 403 it doesn't log a warning per
            # page load.
            return HttpResponse(status=204)

        target, message = self._next_step(request.user)
        messages.error(request, message)
        if request.htmx:
            # Swapping the setup form into a fragment would strand the user, so move the whole page.
            return HttpResponseClientRedirect(target)
        return HttpResponseRedirect(target)

    @staticmethod
    def _next_step(user) -> tuple[str, str]:
        """Where the user has to go to satisfy the requirement, and why.

        allauth refuses to enable MFA while any of the user's email addresses is unverified, and
        bounces the setup page straight back to the MFA overview (see
        ``allauth.mfa.internal.flows.add.validate_can_add_authenticator``). Sending those users to
        the setup page would leave them with no way to satisfy the requirement, so point them at
        their email addresses instead.
        """

        if (
            not mfa_settings.ALLOW_UNVERIFIED_EMAIL
            and EmailAddress.objects.filter(user_id=user.pk, verified=False).exists()
        ):
            return reverse("account_email"), RequireMfaForStaffMiddleware.VERIFY_EMAIL_MESSAGE
        return reverse("mfa_activate_totp"), RequireMfaForStaffMiddleware.MESSAGE

    def _is_exempt(self, request) -> bool:
        if self._is_exempt_path(request.path):
            return True

        view_name = request.resolver_match.view_name if request.resolver_match else None
        return view_name in self.ALLOWED_VIEW_NAMES

    def _is_exempt_path(self, path: str) -> bool:
        if not path:
            return False
        if path.startswith(self.EXEMPT_PATH_PREFIXES):
            return True
        # Assets are served ahead of this middleware in production, but not by the dev server.
        asset_prefixes = tuple(url for url in (settings.STATIC_URL, settings.MEDIA_URL) if url and url != "/")
        return path.startswith(asset_prefixes)
