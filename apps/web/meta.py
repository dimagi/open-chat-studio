from functools import partial

from django.conf import settings
from django.contrib.sites.models import Site
from django.urls import reverse


def get_protocol(is_secure: bool = settings.USE_HTTPS_IN_ABSOLUTE_URLS) -> str:
    """
    Returns the default protocol for the server ("http" or "https").
    """
    return f"http{'s' if is_secure else ''}"


def get_server_root(is_secure: bool = settings.USE_HTTPS_IN_ABSOLUTE_URLS) -> str:
    """
    Returns the default server root, with protocol. E.g. https://www.example.com
    """
    return f"{get_protocol(is_secure)}://{Site.objects.get_current().domain}"


def absolute_url(relative_url: str, is_secure: bool = settings.USE_HTTPS_IN_ABSOLUTE_URLS):
    """
    Returns the complete absolute url for a given path - for use in emails or API integrations.
    """
    if settings.DEBUG and settings.SITE_URL_ROOT:
        return f"{settings.SITE_URL_ROOT}{relative_url}"

    return f"{get_server_root(is_secure)}{relative_url}"


websocket_reverse = partial(reverse, urlconf="gpt_playground.channels_urls")


def websocket_absolute_url(relative_url: str, is_secure: bool = settings.USE_HTTPS_IN_ABSOLUTE_URLS):
    """
    Returns the complete absolute url for a given path - for use in emails or API integrations.
    """
    http_url = absolute_url(relative_url, is_secure)
    return "ws" + http_url[4:]
