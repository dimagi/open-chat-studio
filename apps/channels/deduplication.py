"""Lookups behind inbound delivery deduplication, against `ChatMessage.external_ids`.

Callers are `DuplicateDeliveryStage`, which is where the mechanism is documented, plus the
Connect view and the email handler. Channels build their ids with `external_ids_for` in `parse()`;
Connect is the one exception, filtering raw dicts before anything is parsed.

The namespace prefixes are deliberately independent of `ChannelPlatform`: one "whatsapp" covers
both Meta and Turn.io, "twilio" names a provider rather than a platform, and a stored id has to keep
its meaning even if a platform enum value is renamed later.
"""

from itertools import chain

from apps.chat.models import ChatMessage


def namespaced_id(platform: str, *parts) -> str:
    """Build the stored form of a provider message id."""
    return ":".join([platform, *(str(part) for part in parts)])


def external_ids_for(platform: str, *parts) -> list[str]:
    """The ids to store for a delivery, empty when the provider did not fully identify it.

    Every channel goes through here rather than calling `namespaced_id` directly, because storing a
    `None` sentinel is this feature's worst failure mode: a literal "slack:None" would be recorded
    for the first message that lacks an id and silently drop every later one.
    """
    if any(part is None or part == "" for part in parts):
        return []
    return [namespaced_id(platform, *parts)]


def unseen_message_ids(external_ids: list[str], team_id: int) -> set[str]:
    """Return the ids with no ChatMessage in this team already recording them."""
    candidates = set(external_ids)
    if not candidates:
        return candidates

    seen = chain.from_iterable(
        ChatMessage.objects.filter(chat__team_id=team_id, external_ids__overlap=list(candidates)).values_list(
            "external_ids", flat=True
        )
    )
    return candidates - set(seen)


def is_duplicate_delivery(external_ids: list[str], team_id: int) -> bool:
    """Whether every id on this delivery has already been recorded.

    False for an empty list: a delivery the provider did not identify is processed, never dropped.
    That is why callers must use this rather than negating `unseen_message_ids`, which would invert
    the intent with no visible symptom.
    """
    return bool(external_ids) and not unseen_message_ids(external_ids, team_id)


def connect_external_ids(messages: list[dict]) -> list[str]:
    return [namespaced_id("connect", message["message_id"]) for message in messages]
