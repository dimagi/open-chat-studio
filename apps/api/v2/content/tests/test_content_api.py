"""The source material and consent form endpoints (#4145): behaviour both resources share."""

import pytest

from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

from .conftest import RESOURCES


@pytest.mark.django_db()
@pytest.mark.parametrize("resource", RESOURCES)
class TestContentResources:
    def test_create(self, client, team, resource):
        response = client.post(resource.list_url, resource.create_body, format="json")

        assert response.status_code == 201, response.content
        body = response.json()
        row = resource.model.objects.get(pk=body["id"])
        assert row.team == team
        assert row.is_working_version
        assert {key: body[key] for key in resource.create_body} == resource.create_body

    def test_create_rejects_unknown_keys(self, client, resource):
        response = client.post(resource.list_url, {**resource.create_body, "team": 1}, format="json")

        assert response.status_code == 400
        assert "team" in response.json()

    def test_list_holds_only_the_teams_working_rows(self, client, team, resource):
        # A new team is given a default consent form.
        existing = list(resource.model.objects.filter(team=team).values_list("id", flat=True))
        mine = resource.factory.create(team=team)
        mine.create_new_version()
        resource.factory.create(team=team, is_archived=True)
        resource.factory.create(team=TeamWithUsersFactory.create())

        response = client.get(resource.list_url)

        assert response.status_code == 200
        assert sorted(row["id"] for row in response.json()["results"]) == sorted([*existing, mine.id])

    def test_retrieve(self, client, team, resource):
        row = resource.factory.create(team=team)

        response = client.get(resource.detail_url(row.id))

        assert response.status_code == 200
        assert response.json()["id"] == row.id

    @pytest.mark.parametrize("method", ["get", "patch", "delete"])
    def test_another_teams_row_is_not_found(self, client, resource, method):
        other = resource.factory.create(team=TeamWithUsersFactory.create())

        response = getattr(client, method)(resource.detail_url(other.id), {}, format="json")

        assert response.status_code == 404

    @pytest.mark.parametrize("method", ["get", "patch", "delete"])
    def test_a_published_snapshot_is_not_reachable(self, client, team, resource, method):
        """Snapshots are immutable; writes only reach the working version."""
        version = resource.factory.create(team=team).create_new_version()

        response = getattr(client, method)(resource.detail_url(version.id), {}, format="json")

        assert response.status_code == 404

    def test_patch_changes_only_the_keys_sent(self, client, team, resource):
        row = resource.factory.create(team=team)
        before = client.get(resource.detail_url(row.id)).json()

        response = client.patch(resource.detail_url(row.id), {resource.patch_field: "Renamed"}, format="json")

        assert response.status_code == 200, response.content
        assert response.json() == {**before, resource.patch_field: "Renamed"}
        row.refresh_from_db()
        assert getattr(row, resource.patch_field) == "Renamed"

    def test_patch_rejects_unknown_keys(self, client, team, resource):
        row = resource.factory.create(team=team)

        response = client.patch(resource.detail_url(row.id), {"is_archived": True}, format="json")

        assert response.status_code == 400
        row.refresh_from_db()
        assert row.is_archived is False

    def test_put_is_not_offered(self, client, team, resource):
        row = resource.factory.create(team=team)

        response = client.put(resource.detail_url(row.id), resource.create_body, format="json")

        assert response.status_code == 405

    def test_delete_archives_rather_than_destroys(self, client, team, resource):
        row = resource.factory.create(team=team)

        response = client.delete(resource.detail_url(row.id))

        assert response.status_code == 200, response.content
        assert response.json() == {"archived": True}
        archived = resource.model.objects.get_all().get(pk=row.id)
        assert archived.is_archived is True

    def test_a_repeated_delete_is_not_found(self, client, team, resource):
        row = resource.factory.create(team=team)
        client.delete(resource.detail_url(row.id))

        assert client.delete(resource.detail_url(row.id)).status_code == 404

    def test_options_describes_the_create_body(self, client, resource):
        actions = client.options(resource.list_url).json()["actions"]

        assert set(resource.create_body) <= set(actions["POST"])
        assert actions["POST"]["id"]["read_only"] is True

    def test_options_describes_the_patch_body(self, client, team, resource):
        row = resource.factory.create(team=team)

        actions = client.options(resource.detail_url(row.id)).json()["actions"]

        assert set(actions) == {"PATCH"}


@pytest.mark.django_db()
@pytest.mark.parametrize("resource", RESOURCES)
class TestReadOnlyKey:
    @pytest.fixture()
    def read_only_client(self, team):
        return ApiTestClient(team.members.first(), team, read_only=True)

    def test_may_read(self, read_only_client, team, resource):
        row = resource.factory.create(team=team)

        assert read_only_client.get(resource.list_url).status_code == 200
        assert read_only_client.get(resource.detail_url(row.id)).status_code == 200

    def test_may_not_create(self, read_only_client, resource):
        response = read_only_client.post(resource.list_url, resource.create_body, format="json")

        assert response.status_code == 403
        assert not resource.model.objects.filter(**resource.create_body).exists()

    @pytest.mark.parametrize("method", ["patch", "delete"])
    def test_may_not_change(self, read_only_client, team, resource, method):
        row = resource.factory.create(team=team)

        response = getattr(read_only_client, method)(
            resource.detail_url(row.id), {resource.patch_field: "No"}, format="json"
        )

        assert response.status_code == 403
        row.refresh_from_db()
        assert getattr(row, resource.patch_field) != "No"
        assert row.is_archived is False
