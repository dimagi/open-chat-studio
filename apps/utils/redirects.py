from django.utils.http import url_has_allowed_host_and_scheme


def safe_redirect_url(url: str, fallback: str = "/") -> str:
    """Return `url` if it is a path on this site, otherwise `fallback`."""
    if not url or not url_has_allowed_host_and_scheme(url, allowed_hosts=None):
        return fallback
    return url
