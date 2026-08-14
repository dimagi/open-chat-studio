"""Rate limit keying for the inbound channel webhooks.

Each route counts under its own identity type, so no two routes share a keyspace.
A route whose URL carries a channel identifier buckets on that identifier, so one
noisy channel cannot consume the allowance of every other channel behind the same
provider address; a route whose URL carries no identifier buckets on the caller's
address under an identity type of its own. A route reached without the identifier
its URL normally carries falls back to the plain address bucket.
"""

from apps.utils.rate_limit import client_ip

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
