"""Rate limit key functions for the inbound channel webhooks.

Each webhook buckets on the channel identifier its URL carries, so one noisy
channel cannot consume the allowance of every other channel behind the same
provider IP. Webhooks whose URL carries no identifier fall back to the caller's
address.
"""

from apps.utils.rate_limit import client_ip


def _url_identifier_key(request, kwarg_name, kwargs):
    identifier = kwargs.get(kwarg_name)
    if identifier is None:
        return "ip", client_ip(request)
    return "channel", str(identifier)


def channel_external_id_key(request, *args, **kwargs):
    return _url_identifier_key(request, "channel_external_id", kwargs)


def experiment_id_key(request, *args, **kwargs):
    return _url_identifier_key(request, "experiment_id", kwargs)


def sureadhere_tenant_key(request, *args, **kwargs):
    return _url_identifier_key(request, "sureadhere_tenant_id", kwargs)
