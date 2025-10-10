import pytest
from django.urls import reverse

from apps.events.models import EventActionType, TimePeriod
from apps.experiments.models import Participant, ParticipantData
from apps.utils.factories.events import EventActionFactory, ScheduledMessageFactory
from apps.utils.factories.experiment import (
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
)


@pytest.mark.django_db()
class TestEditIdentifier:
    """Tests for the edit_identifier view."""

    def test_update_identifier_success(self, client, team_with_users):
        """Test successfully updating a participant's identifier when no conflict exists."""
        participant = ParticipantFactory(team=team_with_users, identifier="old_identifier", platform="web")
        user = team_with_users.members.first()
        client.login(username=user.username, password="password")

        url = reverse(
            "participants:edit_identifier",
            kwargs={"team_slug": team_with_users.slug, "pk": participant.id},
        )

        response = client.post(url, {"identifier": "new_identifier"})

        # Should redirect to single participant home
        assert response.status_code == 302
        assert response.url == reverse(
            "participants:single-participant-home",
            kwargs={"team_slug": team_with_users.slug, "participant_id": participant.id},
        )

        # Verify identifier was updated
        participant.refresh_from_db()
        assert participant.identifier == "new_identifier"

    def test_merge_participants_on_identifier_conflict(self, client, team_with_users):
        """Test merging participants when updating identifier conflicts with existing participant."""
        experiment = ExperimentFactory(team=team_with_users)

        # Create the participant we'll be updating
        old_participant = ParticipantFactory(team=team_with_users, identifier="old_identifier", platform="web")
        ParticipantData.objects.create(
            team=team_with_users,
            participant=old_participant,
            experiment=experiment,
            data={"key1": "value1"},
            system_metadata={"meta1": "data1"},
        )
        old_session = ExperimentSessionFactory(team=team_with_users, participant=old_participant, experiment=experiment)

        params = {
            "name": "Test",
            "time_period": TimePeriod.DAYS,
            "frequency": 1,
            "repetitions": 1,
            "prompt_text": "",
            "experiment_id": experiment.id,
        }
        event_action = EventActionFactory(params=params, action_type=EventActionType.SCHEDULETRIGGER)

        old_schedule = ScheduledMessageFactory(
            team=team_with_users, participant=old_participant, experiment=experiment, action=event_action
        )

        # Create the existing participant with the target identifier
        existing_participant = ParticipantFactory(
            team=team_with_users, identifier="existing_identifier", platform="web"
        )
        existing_data = ParticipantData.objects.create(
            team=team_with_users,
            participant=existing_participant,
            experiment=experiment,
            data={"key2": "value2"},
            system_metadata={"meta2": "data2"},
        )

        user = team_with_users.members.first()
        client.login(username=user.username, password="password")

        url = reverse(
            "participants:edit_identifier",
            kwargs={"team_slug": team_with_users.slug, "pk": old_participant.id},
        )

        response = client.post(url, {"identifier": "existing_identifier"})

        # Should redirect to the existing participant's home page
        assert response.status_code == 302
        assert response.url == reverse(
            "participants:single-participant-home",
            kwargs={"team_slug": team_with_users.slug, "participant_id": existing_participant.id},
        )

        # Old participant should be deleted
        assert not Participant.objects.filter(id=old_participant.id).exists()

        # Existing participant should still exist
        assert Participant.objects.filter(id=existing_participant.id).exists()

        # Data should be merged
        existing_data.refresh_from_db()
        assert existing_data.data == {"key1": "value1", "key2": "value2"}
        assert existing_data.system_metadata == {"meta1": "data1", "meta2": "data2"}

        # Sessions should be transferred
        old_session.refresh_from_db()
        assert old_session.participant_id == existing_participant.id

        # Scheduled messages should be transferred
        old_schedule.refresh_from_db()
        assert old_schedule.participant_id == existing_participant.id

    def test_no_change_when_identifier_same(self, client, team_with_users):
        """Test that no changes occur when the identifier is the same as current."""
        participant = ParticipantFactory(team=team_with_users, identifier="same_identifier", platform="web")
        user = team_with_users.members.first()
        client.login(username=user.username, password="password")

        url = reverse(
            "participants:edit_identifier",
            kwargs={"team_slug": team_with_users.slug, "pk": participant.id},
        )

        response = client.post(url, {"identifier": "same_identifier"})

        # Should redirect to single participant home
        assert response.status_code == 302
        assert response.url == reverse(
            "participants:single-participant-home",
            kwargs={"team_slug": team_with_users.slug, "participant_id": participant.id},
        )

        # Verify identifier unchanged
        participant.refresh_from_db()
        assert participant.identifier == "same_identifier"
