from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import RedirectView

from apps.web.waf import WafRule, waf_allow


@waf_allow(WafRule.NoUserAgent_HEADER)
class PreloginRedirectView(RedirectView):
    """A prelogin redirect, excluded from the NoUserAgent_HEADER WAF rule.

    Prelogin URLs are public and get hit by clients that don't send a User-Agent. `waf_allow`
    keys class-based views on the class, so this subclass exists to scope the exception to the
    prelogin URLs — decorating `RedirectView` itself would pull every other use of it in the
    project into the exception list too.
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
        return render(request, "prelogin/landing.html")
