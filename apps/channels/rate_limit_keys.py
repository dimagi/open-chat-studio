"""Rate limit keying and over-limit responses for the inbound channel webhooks.

Each route counts under its own identity type, so no two routes share a keyspace.
A route whose URL carries a channel identifier buckets on that identifier, so one
noisy channel cannot consume the allowance of every other channel behind the same
provider address; a route whose URL carries no identifier buckets on the caller's
address under an identity type of its own. A route reached without the identifier
its URL normally carries falls back to the plain address bucket.
"""

from django.http import HttpResponse

from apps.utils.rate_limit import RateLimitResult, client_ip, rate_limited

CHANNELS_SCOPE = "channels"


def _url_identifier_key(request, kwarg_name, kwargs, identity_type):
    identifier = kwargs.get(kwarg_name)
    if identifier is None:
        return "ip", client_ip(request)
    return identity_type, str(identifier)


def channel_external_id_key(request, *args, **kwargs):
    return _url_identifier_key(request, "channel_external_id", kwargs, "telegram_channel")


def experiment_id_key(request, *args, **kwargs):
    return _url_identifier_key(request, "experiment_id", kwargs, "turn_experiment")


def sureadhere_tenant_key(request, *args, **kwargs):
    return _url_identifier_key(request, "sureadhere_tenant_id", kwargs, "sureadhere_tenant")


def _client_address_key(identity_type):
    """Builds a key function bucketing on the caller's address under `identity_type`."""

    def key_fn(request, *args, **kwargs):
        return identity_type, client_ip(request)

    return key_fn


twilio_ip_key = _client_address_key("twilio_ip")
meta_ip_key = _client_address_key("meta_ip")
connect_ip_key = _client_address_key("connect_ip")
slack_ip_key = _client_address_key("slack_ip")


def meta_limited_response(request, result: RateLimitResult) -> HttpResponse:
    """Answers an over-limit Meta delivery with 200 and an empty body, matching every
    other delivery that route drops.

    Meta disables the webhook subscription for a whole WhatsApp Business Account after
    sustained non-2xx responses, which stops inbound WhatsApp for every team that
    account serves. Counting and the log-only signal are unaffected.
    """
    return HttpResponse()


def meta_webhook_rate_limited(view_func):
    """Applies the webhook scope, Meta's address keying and its 200 over-limit response."""
    return rate_limited(CHANNELS_SCOPE, key_fn=meta_ip_key, response_fn=meta_limited_response)(view_func)
