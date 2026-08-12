"""Rate limit key functions for the inbound channel webhooks.

Each webhook buckets on the channel identifier its URL carries, so one noisy
channel cannot consume the allowance of every other channel behind the same
provider IP. Each route uses its own identity type, so the three routes never
share a keyspace even when their URL identifiers collide. Webhooks whose URL
carries no identifier fall back to the caller's address.
"""

from apps.utils.rate_limit import client_ip


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
