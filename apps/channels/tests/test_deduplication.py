import pytest

from apps.channels.deduplication import (
    _unseen_message_ids,
    external_ids_for,
    is_duplicate_delivery,
    namespaced_id,
)
from apps.utils.factories.experiment import ExperimentFactory
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

    unseen = _unseen_message_ids(["connect:a", "connect:c", "connect:d", "email:<forged@example.com>"], team.id)

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


@pytest.mark.django_db()
class TestChatbotScope:
    """A Telegram `message_id` is unique only within one bot's dialog with one peer, so both halves
    are in the key: `chat.id` for a private chat is the participant's Telegram user id, identical
    across every bot they talk to, and each dialog's `message_id` restarts near 1."""

    def test_another_chatbot_in_the_team_does_not_suppress(self, record_delivery):
        bot_a = ExperimentFactory()
        bot_b = ExperimentFactory(team=bot_a.team)
        record_delivery(bot_a.team, external_ids_for("telegram", bot_a.id, 55501, 1))

        assert is_duplicate_delivery(external_ids_for("telegram", bot_b.id, 55501, 1), bot_a.team_id) is False
        assert is_duplicate_delivery(external_ids_for("telegram", bot_a.id, 55501, 1), bot_a.team_id) is True
