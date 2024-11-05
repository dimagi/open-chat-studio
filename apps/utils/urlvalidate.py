# Copied from CommCare HQ: https://github.com/dimagi/commcare-hq/blob/master/corehq/util/urlvalidate/urlvalidate.py#L6
import logging
from urllib.parse import urlparse

from .ip_resolver import CannotResolveHost, resolve_to_ips

log = logging.getLogger(__name__)


def validate_user_input_url(url, allow_http=False):
    """
    Raises an exception if the supplied URL is considered invalid or unsafe

    raise InvalidURL if `url` is not a valid URL or can't be resolved
    """
    try:
        _validate_url(url, allow_http)
    except PossibleSSRFAttempt as e:
        log.exception(
            "Error resolving host: %s",
            str(e),
            extra={
                "url": url,
            },
        )
        raise InvalidURL("Invalid URL. Ensure that the URL is a valid HTTPS URL")
    except CannotResolveHost as e:
        log.exception(
            "Error resolving host: %s",
            str(e),
            extra={
                "url": url,
            },
        )
        raise InvalidURL("Unable to validate URL")


def _validate_url(url, allow_http=False):
    parsed_url = urlparse(url)
    hostname = parsed_url.hostname
    scheme = parsed_url.scheme
    if hostname is None:
        raise InvalidURL("URL must be an absolute URL")
    allowed_schemes = ["https"]
    if allow_http:
        allowed_schemes.append("http")
    if scheme not in allowed_schemes:
        raise PossibleSSRFAttempt("invalid scheme")

    for ip_address in resolve_to_ips(hostname, parsed_url.port or 80):
        sanitize_ip(ip_address)


def sanitize_ip(ip_address):
    if ip_address.is_loopback:
        raise PossibleSSRFAttempt("is_loopback")
    elif ip_address.is_reserved:
        raise PossibleSSRFAttempt("is_reserved")
    elif ip_address.is_link_local:
        raise PossibleSSRFAttempt("is_link_local")
    elif ip_address.is_multicast:
        raise PossibleSSRFAttempt("is_multicast")
    elif ip_address.is_private:
        raise PossibleSSRFAttempt("is_private")
    elif not ip_address.is_global:
        raise PossibleSSRFAttempt("not is_global")
    else:
        return ip_address


class PossibleSSRFAttempt(Exception):
    def __init__(self, reason):
        super().__init__("Invalid URL")
        self.reason = reason


class InvalidURL(Exception):
    pass
