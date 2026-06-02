"""Resolve who/what applied an eval-driven tag, for the tag tooltip.

Eval tags are written as `CustomTaggedItem`s with no `user`, so the generic tag
tooltip would otherwise attribute them to "Participant". The active `AppliedTag` audit
row — one whose run is not archived (`evaluation_result__run__tags_archived=False`) — is
the source of truth for which evaluator + run applied a live eval tag.
`attach_tag_attributions` resolves that per tagged object and attaches a `{tag_id: label}`
dict the `TaggedModelMixin.prefetched_tags_json` reads.
"""

from __future__ import annotations

from collections import defaultdict

from apps.evaluations.models import AppliedTag

# Lookup paths from AppliedTag to the live tag target, mirroring resolve_target():
# SESSION mode tags the session's Chat; MESSAGE mode tags the expected-output ChatMessage.
_CHAT_PATH = "evaluation_result__message__session__chat_id"
_MESSAGE_PATH = "evaluation_result__message__expected_output_chat_message_id"


def _label(evaluator_name: str, run_id: int) -> str:
    return f"evaluator '{evaluator_name}' (run #{run_id})"


def _build_attributions(target_ids: list[int], target_path: str) -> dict[int, dict[int, str]]:
    """Map each target id to ``{tag_id: label}`` from its active AppliedTag rows.

    Ordered by ascending run id so that when more than one active row applies the
    same tag to the same target, the latest run's attribution wins.
    """
    if not target_ids:
        return {}

    rows = (
        AppliedTag.objects.filter(evaluation_result__run__tags_archived=False, **{f"{target_path}__in": target_ids})
        .order_by("evaluation_result__run_id")
        .values_list(target_path, "tag_id", "rule__evaluator__name", "evaluation_result__run_id")
    )
    mapping: defaultdict[int, dict[int, str]] = defaultdict(dict)
    for target_id, tag_id, evaluator_name, run_id in rows:
        mapping[target_id][tag_id] = _label(evaluator_name, run_id)
    return mapping


def attach_tag_attributions(objects) -> None:
    """Attach ``prefetched_tag_attributions`` (``{tag_id: label}``) to each tagged object.

    Accepts a mix of `Chat` and `ChatMessage` instances (the two eval tag targets);
    other objects are ignored. Objects with no eval-applied tags get an empty dict.
    """
    from apps.chat.models import Chat, ChatMessage  # noqa: PLC0415 — avoid circular import

    objects = list(objects)
    chats = [o for o in objects if isinstance(o, Chat)]
    messages = [o for o in objects if isinstance(o, ChatMessage)]

    chat_map = _build_attributions([c.id for c in chats], _CHAT_PATH)
    message_map = _build_attributions([m.id for m in messages], _MESSAGE_PATH)

    for chat in chats:
        chat.prefetched_tag_attributions = dict(chat_map.get(chat.id, {}))
    for message in messages:
        message.prefetched_tag_attributions = dict(message_map.get(message.id, {}))
