import pytest
from django.urls import reverse

from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory


@pytest.mark.django_db()
class TestAllParticipantIdentifiers:
    """Tests for the all_participant_identifiers and participant_identifiers_by_experiment views."""

    def _url(self, team):
        return reverse("participants:all_participant_identifiers", kwargs={"team_slug": team.slug})

    def _login(self, client, team):
        user = team.members.first()
        client.login(username=user.username, password="password")
        return user

    def test_returns_identifiers_and_remote_ids(self, client, team_with_users):
        team = team_with_users
        self._login(client, team)
        ParticipantFactory.create(team=team, identifier="alice@example.com", remote_id="remote-1")
        ParticipantFactory.create(team=team, identifier="bob@example.com", remote_id="")

        response = client.get(self._url(team))

        assert response.status_code == 200
        data = response.json()
        assert set(data["identifiers"]) == {"alice@example.com", "bob@example.com"}
        assert data["remote_ids"] == ["remote-1"]
        assert data["truncated"] is False

    def test_search_filters_by_identifier(self, client, team_with_users):
        team = team_with_users
        self._login(client, team)
        ParticipantFactory.create(team=team, identifier="alice@example.com", remote_id="")
        ParticipantFactory.create(team=team, identifier="bob@example.com", remote_id="")

        response = client.get(self._url(team), {"q": "alice"})

        data = response.json()
        assert data["identifiers"] == ["alice@example.com"]
        assert data["remote_ids"] == []
        assert data["truncated"] is False

    def test_search_filters_by_remote_id(self, client, team_with_users):
        team = team_with_users
        self._login(client, team)
        ParticipantFactory.create(team=team, identifier="alice@example.com", remote_id="match-remote")
        ParticipantFactory.create(team=team, identifier="bob@example.com", remote_id="other-remote")

        response = client.get(self._url(team), {"q": "match"})

        data = response.json()
        assert data["identifiers"] == []
        assert data["remote_ids"] == ["match-remote"]

    def test_search_is_case_insensitive(self, client, team_with_users):
        team = team_with_users
        self._login(client, team)
        ParticipantFactory.create(team=team, identifier="Alice@Example.com", remote_id="")

        response = client.get(self._url(team), {"q": "alice"})

        assert response.json()["identifiers"] == ["Alice@Example.com"]

    def test_truncated_flag_set_when_results_exceed_limit(self, client, team_with_users, monkeypatch):
        monkeypatch.setattr("apps.participants.views.PARTICIPANT_IDENTIFIER_LIMIT", 2)
        team = team_with_users
        self._login(client, team)
        for i in range(3):
            ParticipantFactory.create(team=team, identifier=f"user{i}@example.com", remote_id="")

        response = client.get(self._url(team))

        data = response.json()
        assert len(data["identifiers"]) == 2
        assert data["truncated"] is True

    def test_not_truncated_when_distinct_values_fit_limit(self, client, team_with_users, monkeypatch):
        monkeypatch.setattr("apps.participants.views.PARTICIPANT_IDENTIFIER_LIMIT", 2)
        team = team_with_users
        self._login(client, team)
        for i in range(3):
            ParticipantFactory.create(team=team, identifier=f"user{i}@example.com", remote_id="shared")

        data = client.get(self._url(team), {"q": "shared"}).json()

        assert data["remote_ids"] == ["shared"]
        assert data["truncated"] is False

    def test_does_not_leak_other_team_participants(self, client, team_with_users):
        team = team_with_users
        self._login(client, team)
        ParticipantFactory.create(team=team, identifier="mine@example.com", remote_id="")
        ParticipantFactory.create(identifier="other@example.com", remote_id="")  # different team

        data = client.get(self._url(team)).json()
        assert data["identifiers"] == ["mine@example.com"]

    def test_scoped_to_experiment(self, client, team_with_users):
        team = team_with_users
        self._login(client, team)
        experiment = ExperimentFactory.create(team=team)
        in_session = ParticipantFactory.create(team=team, identifier="in@example.com", remote_id="")
        ExperimentSessionFactory.create(team=team, experiment=experiment, participant=in_session)
        ParticipantFactory.create(team=team, identifier="out@example.com", remote_id="")

        url = reverse(
            "participants:participant_identifier",
            kwargs={"team_slug": team.slug, "experiment_id": experiment.id},
        )
        data = client.get(url).json()
        assert data["identifiers"] == ["in@example.com"]
