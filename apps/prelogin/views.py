from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.web.waf import WafRule, waf_allow


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
