from copy import copy

from django.conf import settings

from .meta import absolute_url, get_server_root
from .superuser_utils import get_temporary_superuser_access


def project_meta(request):
    # modify these values as needed and add whatever else you want globally available here
    project_data = copy(settings.PROJECT_METADATA)
    project_data["TITLE"] = "{} | {}".format(project_data["NAME"], project_data["DESCRIPTION"])
    return {
        "project_meta": project_data,
        "server_url": get_server_root(),
        "page_url": absolute_url(request.path),
        "page_title": "",
        "page_description": "",
        "page_image": "",
        # put any settings you want made available to all templates here
        # then reference them as {{ project_settings.MY_VALUE }} in templates
        "project_settings": {
            "ACCOUNT_SIGNUP_PASSWORD_ENTER_TWICE": any(
                "password2" in field for field in settings.ACCOUNT_SIGNUP_FIELDS
            ),
        },
        "use_i18n": getattr(settings, "USE_I18N", False) and len(getattr(settings, "LANGUAGES", [])) > 1,
        "signup_enabled": settings.SIGNUP_ENABLED,
        "temporary_superuser_access": get_temporary_superuser_access(request),
        "docs_base_url": settings.DOCUMENTATION_BASE_URL,
        "docs_links": settings.DOCUMENTATION_LINKS,
        "dark_mode": request.COOKIES.get("theme", "") == "dark",
        "login_url_name": settings.LOGIN_URL,
        "debug": settings.DEBUG,
    }


def google_analytics_id(request):
    """
    Adds google analytics id to all requests
    """
    if settings.GOOGLE_ANALYTICS_ID:
        return {
            "GOOGLE_ANALYTICS_ID": settings.GOOGLE_ANALYTICS_ID,
        }
    else:
        return {}


def unread_notifications_count(request):
    """
    Adds unread notification count to context
    """
    count = 0
    if request.user.is_authenticated and request.team:
        count = request.user.unread_notifications_count(team_slug=request.team.slug)
    return {
        "unread_notifications_count": count,
    }
