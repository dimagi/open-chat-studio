from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import ExperimentSession

if TYPE_CHECKING:
    from apps.teams.models import Team

logger = logging.getLogger("ocs.channels")


def get_email_experiment_channel(
    in_reply_to: str | None,
    references: list[str],
    to_address: str,
    team: Team | None = None,
) -> tuple[ExperimentChannel | None, ExperimentSession | None]:
    """Route an inbound email to the correct channel and session.

    Priority chain (first match wins):
    1. In-Reply-To / References -> existing session lookup
    2. To-address -> ExperimentChannel.extra_data["email_address"]
    3. Default fallback -> extra_data["is_default"] == True (requires team)
    4. No match -> (None, None)
    """
    # Priority 1: Thread continuity via In-Reply-To
    if in_reply_to:
        session = _lookup_session(in_reply_to)
        if session:
            return session.experiment_channel, session

    # Priority 1b: Fallback to References header
    for ref in references:
        session = _lookup_session(ref)
        if session:
            return session.experiment_channel, session

    # Priority 2: To-address match
    channel = (
        ExperimentChannel.objects.filter(
            platform=ChannelPlatform.EMAIL,
            extra_data__contains={"email_address": to_address},
            deleted=False,
        )
        .select_related("experiment", "team")
        .first()
    )
    if channel:
        return channel, None

    # Priority 3: Default fallback (only if team is known)
    if team:
        default = (
            ExperimentChannel.objects.filter(
                platform=ChannelPlatform.EMAIL,
                extra_data__contains={"is_default": True},
                team=team,
                deleted=False,
            )
            .select_related("experiment", "team")
            .first()
        )
        if default:
            return default, None

    # Priority 4: No match
    return None, None


def _lookup_session(message_id: str) -> ExperimentSession | None:
    """Find a session by its external_id (first outbound Message-ID)."""
    try:
        return ExperimentSession.objects.select_related("team", "participant", "experiment_channel").get(
            external_id=message_id
        )
    except ExperimentSession.DoesNotExist:
        return None
