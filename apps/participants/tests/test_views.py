import json

import pytest
from django.http import QueryDict
from django.urls import reverse

from apps.participants.models import ParticipantData
from apps.utils.factories.experiment import ExperimentSessionFactory, ParticipantFactory


@pytest.mark.django_db()
def test_edit_participant_data(client, team_with_users):
    participant = ParticipantFactory(team=team_with_users)
    team = participant.team
    session = ExperimentSessionFactory(participant=participant, team=team, experiment__team=team)
    user = participant.team.members.first()
    data = {"name": "A"}
    participant_data = ParticipantData.objects.create(
        team=team, experiment=session.experiment, participant=participant, data=data
    )
    client.login(username=user.username, password="password")

    url = reverse(
        "participants:edit-participant-data",
        kwargs={
            "team_slug": participant.team.slug,
            "participant_id": participant.id,
            "experiment_id": session.experiment.id,
        },
    )

    data["name"] = "B"
    query_data = QueryDict("", mutable=True)
    query_data.update({"participant-data": json.dumps(data)})
    client.post(url, query_data)
    participant_data.refresh_from_db()
    assert participant_data.data["name"] == "B"
