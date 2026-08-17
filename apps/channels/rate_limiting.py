"""Rate limit accounting for the inbound channel webhooks.

Counting happens inside each view rather than at the door, once the delivery has
resolved to an `ExperimentChannel` and passed whatever signature check the provider
offers. Two things follow from that ordering.

The identity is a local primary key the caller does not supply. A caller varying an
identifier in a URL or a payload therefore creates no counter entries, and cannot
draw down the allowance of a channel it does not own. Keying on what the request
carries would count before anything had authenticated it.

The resolved channel also carries its team, which reaches the over-limit log line,
so an identity that crosses its limit can be attributed without logging the identity
itself.

The cost is that a delivery which never resolves to a channel is not counted at all.
"""

from apps.channels.models import ExperimentChannel
from apps.utils.rate_limit import RateLimitResult, check, count_request

CHANNELS_SCOPE = "channels"

# Buckets are separated by identity type and identity alone, and a channel primary key
# is unique across every platform, so one identity type covers all of them.
CHANNEL_IDENTITY_TYPE = "channel"


def count_channel_delivery(request, channel: ExperimentChannel) -> RateLimitResult:
    """Counts one delivery against the channel it resolved to."""
    return count_request(request, CHANNELS_SCOPE, CHANNEL_IDENTITY_TYPE, channel.pk, team_id=channel.team_id)


def count_channel_message(channel: ExperimentChannel) -> RateLimitResult:
    """Counts one delivery on a route that has no Django request to hand.

    Slack dispatches through Bolt, which resolves the channel from an event rather
    than from the view, so there is no request to record the result on and no
    response of ours to carry the rate limit headers.
    """
    return check(CHANNELS_SCOPE, CHANNEL_IDENTITY_TYPE, str(channel.pk), team_id=channel.team_id)
