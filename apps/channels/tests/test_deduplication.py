import pytest

from apps.channels.deduplication import (
    external_ids_for,
    is_duplicate_delivery,
    namespaced_id,
    unseen_message_ids,
)
from apps.utils.factories.team import TeamFactory


def test_namespaced_id_joins_parts():
    assert namespaced_id("telegram", -100123, 4471) == "telegram:-100123:4471"


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        pytest.param(("wamid.abc",), ["whatsapp:wamid.abc"], id="provider-sent-an-id"),
        pytest.param((None,), [], id="no-id"),
        pytest.param(("",), [], id="blank-id"),
    ],
)
def test_external_ids_for(parts, expected):
    """An unidentified delivery stores nothing; a "whatsapp:None" sentinel would drop every later
    delivery that also lacks an id."""
    assert external_ids_for("whatsapp", *parts) == expected


@pytest.mark.django_db()
def test_returns_only_ids_not_already_recorded_in_this_team(record_delivery):
    """Ids recorded by another team must not suppress this one; senders control email Message-IDs
    and Slack client_msg_ids."""
    team = TeamFactory()
    record_delivery(team, ["connect:a", "connect:b"])
    record_delivery(team, ["connect:c"])
    record_delivery(TeamFactory(), ["email:<forged@example.com>"])

    unseen = unseen_message_ids(["connect:a", "connect:c", "connect:d", "email:<forged@example.com>"], team.id)

    assert unseen == {"connect:d", "email:<forged@example.com>"}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("candidate_ids", "expected"),
    [
        pytest.param([], False, id="no-id-is-never-a-duplicate"),
        pytest.param(["connect:new"], False, id="fresh-id"),
        pytest.param(["connect:a"], True, id="exact-replay"),
        pytest.param(["connect:a", "connect:new"], False, id="partial-overlap-is-not-a-duplicate"),
    ],
)
def test_is_duplicate_delivery(record_delivery, candidate_ids, expected):
    team = TeamFactory()
    record_delivery(team, ["connect:a"])

    assert is_duplicate_delivery(candidate_ids, team.id) is expected
