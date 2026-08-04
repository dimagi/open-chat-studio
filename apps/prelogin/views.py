import logging

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import RedirectView, TemplateView

from apps.web.waf import WafRule, waf_allow

logger = logging.getLogger(__name__)


@waf_allow(WafRule.NoUserAgent_HEADER)
class PreloginTemplateView(TemplateView):
    """A static prelogin page, excluded from the NoUserAgent_HEADER WAF rule.

    Prelogin pages are public and get hit by clients that don't send a User-Agent. `waf_allow`
    keys class-based views on the class, so this subclass exists to scope the exception to the
    prelogin URLs — decorating `TemplateView` itself would pull every other use of it in the
    project into the exception list too.
    """


@waf_allow(WafRule.NoUserAgent_HEADER)
class PreloginRedirectView(RedirectView):
    """A prelogin redirect, excluded from the NoUserAgent_HEADER WAF rule.

    Subclassed for the same reason as `PreloginTemplateView`.
    """


@waf_allow(WafRule.NoUserAgent_HEADER)
def home(request):
    if request.user.is_authenticated:
        team = request.team
        if team:
            return redirect("dashboard:index", team_slug=team.slug)
        else:
            messages.info(
                request,
                _("You are not a member of any teams. Create a new one to get started."),
            )
            return HttpResponseRedirect(reverse("teams:create_team"))
    else:
        return render(request, "prelogin/home.html", {"active_nav": "home"})


def _configured_demo_bots() -> dict:
    """The demo bots from PRELOGIN_DEMO_BOTS that can actually drive a chat widget.

    The setting is operator-supplied JSON, so anything malformed — not an object, an entry
    that isn't an object, an entry missing a required field — is dropped and logged. Those
    cards then render static, which is how an absent entry behaves, rather than 500ing a
    public page or emitting a widget with empty attributes.
    """
    demo_bots = settings.PRELOGIN_DEMO_BOTS
    if not isinstance(demo_bots, dict):
        logger.error("PRELOGIN_DEMO_BOTS must be a JSON object, got %s", type(demo_bots).__name__)
        return {}

    configured = {}
    for key, bot in demo_bots.items():
        if not isinstance(bot, dict):
            logger.error("PRELOGIN_DEMO_BOTS[%r] must be a JSON object, got %s", key, type(bot).__name__)
        elif not (bot.get("id") and bot.get("embed_key")):
            logger.error("PRELOGIN_DEMO_BOTS[%r] is missing 'id' or 'embed_key'", key)
        else:
            configured[key] = bot
    return configured


@waf_allow(WafRule.NoUserAgent_HEADER)
def applications(request):
    return render(
        request,
        "prelogin/applications.html",
        {
            "active_nav": "applications",
            "demo_bots": _configured_demo_bots(),
        },
    )


@waf_allow(WafRule.NoUserAgent_HEADER)
def contact(request):
    hubspot_form = None
    if settings.HUBSPOT_FORM_PORTAL_ID and settings.HUBSPOT_FORM_ID:
        hubspot_form = {
            "region": settings.HUBSPOT_FORM_REGION,
            "portal_id": settings.HUBSPOT_FORM_PORTAL_ID,
            "form_id": settings.HUBSPOT_FORM_ID,
        }
    return render(
        request,
        "prelogin/contact.html",
        {
            "active_nav": "contact",
            "contact_email": settings.PRELOGIN_CONTACT_EMAIL,
            "hubspot_form": hubspot_form,
        },
    )
