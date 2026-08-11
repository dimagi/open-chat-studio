from django.http.request import HttpHeaders
from django.utils.crypto import constant_time_compare

SECRET_HEADER = "X-OCS-Webhook-Secret"


def get_presented_secret(headers: HttpHeaders) -> str:
    """Extract the shared secret an inbound SureAdhere webhook presents, or "" if it presents none.

    SureAdhere does not sign its callbacks, so the only credential available is a secret
    configured on both sides. It is accepted in either the ``X-OCS-Webhook-Secret`` header or
    as an ``Authorization: Bearer`` token, because the callback is registered in SureAdhere's
    own configuration and which of the two it can send is not recorded in this repo.

    Lookups go through ``HttpHeaders``, which is case-insensitive, so header casing does not
    matter. Any other authorization scheme yields "" and is therefore an authentication failure.
    """
    presented = headers.get(SECRET_HEADER, "").strip()
    if presented:
        return presented

    scheme, _, token = headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        return token.strip()
    return ""


def verify_secret(presented: str, expected: str) -> bool:
    """Compare the presented secret against the configured one in constant time.

    Either value being empty is a failure, so a request presenting no credential can never
    authenticate against a provider with no secret configured. Whether such a provider is
    let through at all is the caller's decision, made explicitly.
    """
    return bool(presented) and bool(expected) and constant_time_compare(presented, expected)
