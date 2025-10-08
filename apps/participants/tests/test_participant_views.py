import pytest
from django.urls import reverse

from apps.experiments.models import ExperimentSession, Participant, ParticipantData
from apps.utils.factories.experiment import (
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantDataFactory,
    ParticipantFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team_with_users():
    return TeamWithUsersFactory()


@pytest.fixture()
def participant(team_with_users):
    return ParticipantFactory(team=team_with_users.team, identifier="test@example.com", platform="web")


@pytest.mark.django_db()
class TestDeleteParticipant:
    def test_delete_participant(self, client, team_with_users, participant):
        """Test that a participant can be deleted"""
        client.force_login(team_with_users.owner)
        url = reverse("participants:participant_delete", args=[team_with_users.team.slug, participant.id])

        response = client.delete(url)

        assert response.status_code == 200
        assert not Participant.objects.filter(id=participant.id).exists()

    def test_delete_participant_cascades(self, client, team_with_users, participant):
        """Test that deleting a participant also deletes related data"""
        client.force_login(team_with_users.owner)

        # Create related data
        experiment = ExperimentFactory(team=team_with_users.team)
        session = ExperimentSessionFactory(team=team_with_users.team, participant=participant, experiment=experiment)
        participant_data = ParticipantDataFactory(
            team=team_with_users.team, participant=participant, experiment=experiment
        )

        url = reverse("participants:participant_delete", args=[team_with_users.team.slug, participant.id])

        response = client.delete(url)

        assert response.status_code == 200
        assert not Participant.objects.filter(id=participant.id).exists()
        # Check that related objects are also deleted due to CASCADE
        assert not ExperimentSession.objects.filter(id=session.id).exists()
        assert not ParticipantData.objects.filter(id=participant_data.id).exists()


@pytest.mark.django_db()
class TestEditParticipantIdentifier:
    def test_edit_identifier_simple(self, client, team_with_users, participant):
        """Test editing a participant identifier when there's no conflict"""
        client.force_login(team_with_users.owner)
        url = reverse("participants:edit_identifier", args=[team_with_users.team.slug, participant.id])

        response = client.post(url, {"identifier": "new@example.com"})

        assert response.status_code == 200
        participant.refresh_from_db()
        assert participant.identifier == "new@example.com"

    def test_edit_identifier_empty_fails(self, client, team_with_users, participant):
        """Test that empty identifier is rejected"""
        client.force_login(team_with_users.owner)
        url = reverse("participants:edit_identifier", args=[team_with_users.team.slug, participant.id])

        response = client.post(url, {"identifier": ""})

        assert response.status_code == 200
        assert b"Identifier is required" in response.content
        participant.refresh_from_db()
        assert participant.identifier == "test@example.com"  # Unchanged

    def test_edit_identifier_same_as_current(self, client, team_with_users, participant):
        """Test editing identifier to same value is a no-op"""
        client.force_login(team_with_users.owner)
        url = reverse("participants:edit_identifier", args=[team_with_users.team.slug, participant.id])

        response = client.post(url, {"identifier": participant.identifier})

        assert response.status_code == 200
        participant.refresh_from_db()
        assert participant.identifier == "test@example.com"

    def test_edit_identifier_merge_participants(self, client, team_with_users):
        """Test that editing identifier to existing one merges participants"""
        client.force_login(team_with_users.owner)

        # Create two participants with same platform
        participant1 = ParticipantFactory(team=team_with_users.team, identifier="old@example.com", platform="web")
        participant2 = ParticipantFactory(team=team_with_users.team, identifier="new@example.com", platform="web")

        # Create data for both participants on the same experiment
        experiment = ExperimentFactory(team=team_with_users.team)
        data1 = ParticipantDataFactory(
            team=team_with_users.team,
            participant=participant1,
            experiment=experiment,
            data={"key1": "value1", "shared": "old_value"},
        )
        data2 = ParticipantDataFactory(
            team=team_with_users.team,
            participant=participant2,
            experiment=experiment,
            data={"key2": "value2", "shared": "new_value"},
        )

        # Create sessions for participant1
        session1 = ExperimentSessionFactory(team=team_with_users.team, participant=participant1, experiment=experiment)

        url = reverse("participants:edit_identifier", args=[team_with_users.team.slug, participant1.id])

        response = client.post(url, {"identifier": "new@example.com"})

        assert response.status_code == 200
        assert "HX-Redirect" in response.headers

        # participant1 should be deleted
        assert not Participant.objects.filter(id=participant1.id).exists()

        # participant2 should still exist
        assert Participant.objects.filter(id=participant2.id).exists()

        # Session should be transferred to participant2
        session1.refresh_from_db()
        assert session1.participant_id == participant2.id

        # Data should be merged (participant2's data takes precedence for shared keys)
        merged_data = ParticipantData.objects.get(participant=participant2, experiment=experiment)
        assert merged_data.data["key1"] == "value1"
        assert merged_data.data["key2"] == "value2"
        assert merged_data.data["shared"] == "new_value"  # participant2's value wins

    def test_edit_identifier_merge_different_experiments(self, client, team_with_users):
        """Test merging participants with data on different experiments"""
        client.force_login(team_with_users.owner)

        participant1 = ParticipantFactory(team=team_with_users.team, identifier="old@example.com", platform="web")
        participant2 = ParticipantFactory(team=team_with_users.team, identifier="new@example.com", platform="web")

        experiment1 = ExperimentFactory(team=team_with_users.team)
        experiment2 = ExperimentFactory(team=team_with_users.team)

        # participant1 has data on experiment1
        data1 = ParticipantDataFactory(
            team=team_with_users.team,
            participant=participant1,
            experiment=experiment1,
            data={"exp1_key": "exp1_value"},
        )

        # participant2 has data on experiment2
        data2 = ParticipantDataFactory(
            team=team_with_users.team,
            participant=participant2,
            experiment=experiment2,
            data={"exp2_key": "exp2_value"},
        )

        url = reverse("participants:edit_identifier", args=[team_with_users.team.slug, participant1.id])

        response = client.post(url, {"identifier": "new@example.com"})

        assert response.status_code == 200

        # participant1 should be deleted
        assert not Participant.objects.filter(id=participant1.id).exists()

        # participant2 should have data from both experiments
        assert ParticipantData.objects.filter(participant=participant2, experiment=experiment1).exists()
        assert ParticipantData.objects.filter(participant=participant2, experiment=experiment2).exists()

        exp1_data = ParticipantData.objects.get(participant=participant2, experiment=experiment1)
        assert exp1_data.data["exp1_key"] == "exp1_value"

        exp2_data = ParticipantData.objects.get(participant=participant2, experiment=experiment2)
        assert exp2_data.data["exp2_key"] == "exp2_value"

    def test_edit_identifier_different_platform_no_merge(self, client, team_with_users):
        """Test that participants with different platforms don't merge"""
        client.force_login(team_with_users.owner)

        participant1 = ParticipantFactory(team=team_with_users.team, identifier="user@example.com", platform="web")
        participant2 = ParticipantFactory(team=team_with_users.team, identifier="new@example.com", platform="whatsapp")

        url = reverse("participants:edit_identifier", args=[team_with_users.team.slug, participant1.id])

        response = client.post(url, {"identifier": "new@example.com"})

        assert response.status_code == 200

        # Both should still exist since they have different platforms
        assert Participant.objects.filter(id=participant1.id).exists()
        assert Participant.objects.filter(id=participant2.id).exists()

        participant1.refresh_from_db()
        assert participant1.identifier == "new@example.com"
