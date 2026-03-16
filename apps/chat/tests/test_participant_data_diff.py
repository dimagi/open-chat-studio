import pytest
from django.urls import reverse

from apps.chat.models import ChatMessageType
from apps.utils.factories.experiment import (
    ChatMessageFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
)
from apps.utils.factories.traces import TraceFactory


@pytest.mark.django_db()
class TestParticipantDataDiffInSessionMessages:
    def test_messages_queryset_includes_participant_data_diff(self, client, experiment):
        session = ExperimentSessionFactory.create(
            experiment=experiment,
            participant=ParticipantFactory.create(team=experiment.team, user=experiment.owner),
        )

        messages = []
        for i in range(5):
            msg_type = ChatMessageType.HUMAN if i % 2 == 0 else ChatMessageType.AI
            messages.append(
                ChatMessageFactory.create(
                    chat=session.chat,
                    message_type=msg_type,
                    content=f"Message {i}",
                )
            )

        # Attach traces with participant_data_diff to the 2nd and 4th messages (AI messages)
        diff_1 = [["change", "plan", ["free", "pro"]]]
        diff_2 = [["add", "", [["score", 100]]], ["change", "level", [1, 2]]]
        TraceFactory.create(
            experiment=experiment,
            session=session,
            participant=session.participant,
            team=experiment.team,
            participant_data_diff=diff_1,
            output_message=messages[1],
        )
        TraceFactory.create(
            experiment=experiment,
            session=session,
            participant=session.participant,
            team=experiment.team,
            participant_data_diff=diff_2,
            output_message=messages[3],
        )

        client.force_login(experiment.owner)
        url = reverse(
            "experiments:experiment_session_messages_view",
            kwargs={
                "team_slug": experiment.team.slug,
                "experiment_id": experiment.public_id,
                "session_id": session.external_id,
            },
        )
        response = client.get(url + "?show_all=on")
        assert response.status_code == 200
        page_messages = response.context["messages"]

        assert len(page_messages) == 5
        assert page_messages[0].participant_data_diff_from_trace is None
        assert page_messages[1].participant_data_diff_from_trace == diff_1
        assert page_messages[2].participant_data_diff_from_trace is None
        assert page_messages[3].participant_data_diff_from_trace == diff_2
        assert page_messages[4].participant_data_diff_from_trace is None
