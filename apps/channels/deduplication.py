"""Lookups behind inbound delivery deduplication, against `ChatMessage.external_ids`.

Callers are `DuplicateDeliveryStage`, which is where the mechanism is documented, plus the
Connect view and the email handler. Channels build their ids with `external_ids_for` in `parse()`;
Connect is the one exception, filtering raw dicts before anything is parsed.

The namespace prefixes are deliberately independent of `ChannelPlatform`: one "whatsapp" covers
both Meta and Turn.io, "twilio" names a provider rather than a platform, and a stored id has to keep
its meaning even if a platform enum value is renamed later.
"""

from itertools import chain

from django.db.models import Q

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


def chatbot_id_for(channel) -> int | None:
    """The dedup scope for a delivery that is only known by its `ExperimentChannel`."""
    return channel.experiment.get_working_version_id() if channel.experiment_id else None


def _unseen_message_ids(external_ids: list[str], team_id: int, chatbot_id: int | None = None) -> set[str]:
    """Return the ids with no ChatMessage in this scope already recording them."""
    candidates = set(external_ids)
    if not candidates:
        return candidates

    messages = ChatMessage.objects.filter(chat__team_id=team_id, external_ids__overlap=list(candidates))
    if chatbot_id is not None:
        # A session's experiment may be the working version or any of its published versions.
        messages = messages.filter(
            Q(chat__experiment_session__experiment_id=chatbot_id)
            | Q(chat__experiment_session__experiment__working_version_id=chatbot_id)
        )
    seen = chain.from_iterable(messages.values_list("external_ids", flat=True))
    return candidates - set(seen)


def is_duplicate_delivery(external_ids: list[str], team_id: int, chatbot_id: int | None = None) -> bool:
    """Whether every id on this delivery has already been recorded.

    False for an empty list: a delivery the provider did not identify is processed, never dropped.
    That is why callers must use this rather than negating `_unseen_message_ids`, which would invert
    the intent with no visible symptom.
    """
    return bool(external_ids) and not _unseen_message_ids(external_ids, team_id, chatbot_id)


def connect_external_ids(messages: list[dict]) -> list[str]:
    return [namespaced_id("connect", message["message_id"]) for message in messages]


def unseen_connect_messages(messages: list[dict], team_id: int, chatbot_id: int | None = None) -> list[dict]:
    """The messages in a Connect batch that have not been delivered before.

    Connect is the one channel that filters raw dicts before anything is parsed, because a batch of
    N provider messages becomes a single `ChatMessage`: a partly replayed batch is not a duplicate
    as a whole, so `DuplicateDeliveryStage` would let it through, and by the time the pipeline runs
    the batch has been joined into one `message_text` it could not drop part of anyway.
    """
    unseen = _unseen_message_ids(connect_external_ids(messages), team_id, chatbot_id)
    fresh = []
    for message in messages:
        external_id = namespaced_id("connect", message["message_id"])
        if external_id in unseen:
            # Discarding as we go also collapses an id repeated *within* one batch: it has no
            # recorded delivery yet, so `unseen` alone would let both copies through.
            unseen.discard(external_id)
            fresh.append(message)
    return fresh
