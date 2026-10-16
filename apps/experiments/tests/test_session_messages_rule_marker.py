import pytest
from django.urls import reverse

from apps.chat.models import ChatMessageType
from apps.chat.schemas import ModerationDetail
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory, ParticipantFactory


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("moderation_detail", "expected_marker"),
    [
        pytest.param(ModerationDetail(rules=["markup"]), "rule: markup", id="rule_fired"),
        pytest.param(ModerationDetail(rules=[]), None, id="no_rules"),
        pytest.param(None, None, id="no_detail"),
    ],
)
def test_transcript_rule_marker(client, experiment, moderation_detail, expected_marker):
    session = ExperimentSessionFactory.create(
        experiment=experiment, participant=ParticipantFactory.create(team=experiment.team, user=experiment.owner)
    )
    ChatMessageFactory.create(
        chat=session.chat,
        message_type=ChatMessageType.HUMAN,
        content="hello",
        moderation_detail=moderation_detail,
    )
    client.force_login(experiment.owner)
    url = reverse(
        "experiments:experiment_session_messages_fragment_view",
        kwargs={
            "team_slug": experiment.team.slug,
            "experiment_id": experiment.public_id,
            "session_id": session.external_id,
        },
    )

    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()

    if expected_marker:
        assert expected_marker in content
    else:
        assert "rule:" not in content
