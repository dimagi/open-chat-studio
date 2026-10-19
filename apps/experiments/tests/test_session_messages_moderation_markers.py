import pytest
from django.urls import reverse

from apps.chat.models import ChatMessageType, ModerationStatus
from apps.chat.schemas import ModerationDetail
from apps.experiments.models import SessionStatus
from apps.service_providers.moderation_service.schemas import ModerationCategory, ModerationOutput
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory, ParticipantFactory
from apps.utils.factories.user import UserFactory

FLAGGED_OUTPUT = ModerationOutput(
    {
        "illicit": ModerationCategory(flagged=True, score=0.9),
        "hate/threatening": ModerationCategory(flagged=True, score=0.8),
        "harassment": ModerationCategory(flagged=True, score=0.7),
        "sexual": ModerationCategory(flagged=False, score=0.1),
    }
)


def _create_session(*, experiment, user, moderation_detail, status=SessionStatus.ACTIVE, moderation_status=None):
    session = ExperimentSessionFactory.create(
        experiment=experiment,
        status=status,
        participant=ParticipantFactory.create(team=experiment.team, user=user),
    )
    ChatMessageFactory.create(
        chat=session.chat,
        message_type=ChatMessageType.HUMAN,
        content="hello",
        moderation_detail=moderation_detail,
        moderation_status=moderation_status,
    )
    return session


def _url(*, name, experiment, session):
    return reverse(
        name,
        kwargs={
            "team_slug": experiment.team.slug,
            "experiment_id": experiment.public_id,
            "session_id": session.external_id,
        },
    )


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("moderation_detail", "marker_present"),
    [
        pytest.param(ModerationDetail(rules=["markup"]), True, id="rule_fired"),
        pytest.param(ModerationDetail(rules=[]), False, id="no_rules"),
        pytest.param(None, False, id="no_detail"),
    ],
)
def test_transcript_rule_marker(client, experiment, moderation_detail, marker_present):
    session = _create_session(experiment=experiment, user=None, moderation_detail=moderation_detail)
    client.force_login(experiment.team.members.first())

    response = client.get(
        _url(name="experiments:experiment_session_messages_fragment_view", experiment=experiment, session=session)
    )

    assert response.status_code == 200
    assert ("rule: markup" in response.content.decode()) is marker_present


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("moderation_status", "marker"),
    [
        pytest.param(ModerationStatus.FLAGGED, "flagged: hate/threatening, illicit", id="flagged"),
        pytest.param(ModerationStatus.PASSED, None, id="passed"),
        pytest.param(None, None, id="not_moderated"),
    ],
)
def test_transcript_flagged_marker_lists_strike_categories(client, experiment, moderation_status, marker):
    session = _create_session(
        experiment=experiment,
        user=None,
        moderation_detail=ModerationDetail(output=FLAGGED_OUTPUT),
        moderation_status=moderation_status,
    )
    client.force_login(experiment.team.members.first())

    response = client.get(
        _url(name="experiments:experiment_session_messages_fragment_view", experiment=experiment, session=session)
    )

    content = response.content.decode()
    assert response.status_code == 200
    if marker:
        assert marker in content
    else:
        assert "flagged:" not in content


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("view_name", "status"),
    [
        pytest.param("experiments:experiment_review", SessionStatus.PENDING_REVIEW, id="review_page"),
        pytest.param("experiments:experiment_session_messages_view", SessionStatus.ACTIVE, id="transcript_page"),
        pytest.param(
            "experiments:experiment_session_messages_fragment_view", SessionStatus.ACTIVE, id="transcript_fragment"
        ),
    ],
)
def test_moderation_markers_hidden_from_participant(client, experiment, view_name, status):
    participant_user = UserFactory.create()
    session = _create_session(
        experiment=experiment,
        user=participant_user,
        moderation_detail=ModerationDetail(output=FLAGGED_OUTPUT, rules=["markup"]),
        moderation_status=ModerationStatus.FLAGGED,
        status=status,
    )
    client.force_login(participant_user)

    response = client.get(_url(name=view_name, experiment=experiment, session=session))

    assert response.status_code == 200
    content = response.content.decode()
    assert "hello" in content
    assert "rule: markup" not in content
    assert "flagged:" not in content
