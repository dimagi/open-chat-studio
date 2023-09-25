from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render

from .forms import HijackUserForm


@user_passes_test(lambda u: u.is_superuser, login_url="/404")
def hijack_user(request):
    form = HijackUserForm()
    return render(
        request,
        "support/hijack_user.html",
        {
            "active_tab": "support",
            "form": form,
            "redirect_url": settings.LOGIN_REDIRECT_URL,
        },
    )
