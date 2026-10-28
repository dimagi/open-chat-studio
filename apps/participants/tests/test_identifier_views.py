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

    @pytest.mark.parametrize(
        ("field", "expected"),
        [
            pytest.param("identifier", ["alice@example.com", "bob@example.com"], id="identifier"),
            pytest.param("remote_id", ["remote-1"], id="remote-id-skips-empty"),
        ],
    )
    def test_returns_values_for_field(self, client, team_with_users, field, expected):
        team = team_with_users
        self._login(client, team)
        ParticipantFactory.create(team=team, identifier="alice@example.com", remote_id="remote-1")
        ParticipantFactory.create(team=team, identifier="bob@example.com", remote_id="")

        response = client.get(self._url(team), {"field": field})

        assert response.status_code == 200
        assert response.json() == {"values": expected, "has_more": False}

    @pytest.mark.parametrize(
        "field",
        [
            pytest.param(None, id="missing"),
            pytest.param("name", id="not-allowed"),
        ],
    )
    def test_rejects_invalid_field(self, client, team_with_users, field):
        team = team_with_users
        self._login(client, team)

        params = {"field": field} if field else {}
        response = client.get(self._url(team), params)

        assert response.status_code == 400

    @pytest.mark.parametrize(
        ("field", "query", "expected"),
        [
            pytest.param("identifier", "alice", ["Alice@Example.com"], id="identifier-case-insensitive"),
            pytest.param("remote_id", "match", ["match-remote"], id="remote-id"),
        ],
    )
    def test_search(self, client, team_with_users, field, query, expected):
        team = team_with_users
        self._login(client, team)
        ParticipantFactory.create(team=team, identifier="Alice@Example.com", remote_id="match-remote")
        ParticipantFactory.create(team=team, identifier="bob@example.com", remote_id="other-remote")

        data = client.get(self._url(team), {"field": field, "q": query}).json()

        assert data["values"] == expected

    def test_pages_through_values(self, client, team_with_users, monkeypatch):
        monkeypatch.setattr("apps.participants.views.PARTICIPANT_IDENTIFIER_LIMIT", 2)
        team = team_with_users
        self._login(client, team)
        for i in range(3):
            ParticipantFactory.create(team=team, identifier=f"user{i}@example.com")

        first = client.get(self._url(team), {"field": "identifier"}).json()
        second = client.get(self._url(team), {"field": "identifier", "after": first["values"][-1]}).json()

        assert first == {"values": ["user0@example.com", "user1@example.com"], "has_more": True}
        assert second == {"values": ["user2@example.com"], "has_more": False}

    def test_has_more_false_when_distinct_values_fit_limit(self, client, team_with_users, monkeypatch):
        monkeypatch.setattr("apps.participants.views.PARTICIPANT_IDENTIFIER_LIMIT", 2)
        team = team_with_users
        self._login(client, team)
        for i in range(3):
            ParticipantFactory.create(team=team, identifier=f"user{i}@example.com", remote_id="shared")

        data = client.get(self._url(team), {"field": "remote_id"}).json()

        assert data == {"values": ["shared"], "has_more": False}

    def test_does_not_leak_other_team_participants(self, client, team_with_users):
        team = team_with_users
        self._login(client, team)
        ParticipantFactory.create(team=team, identifier="mine@example.com")
        ParticipantFactory.create(identifier="other@example.com")  # different team

        data = client.get(self._url(team), {"field": "identifier"}).json()
        assert data["values"] == ["mine@example.com"]

    def test_scoped_to_experiment(self, client, team_with_users):
        team = team_with_users
        self._login(client, team)
        experiment = ExperimentFactory.create(team=team)
        in_session = ParticipantFactory.create(team=team, identifier="in@example.com")
        ExperimentSessionFactory.create(team=team, experiment=experiment, participant=in_session)
        ParticipantFactory.create(team=team, identifier="out@example.com")

        url = reverse(
            "participants:participant_identifier",
            kwargs={"team_slug": team.slug, "experiment_id": experiment.id},
        )
        data = client.get(url, {"field": "identifier"}).json()
        assert data["values"] == ["in@example.com"]
