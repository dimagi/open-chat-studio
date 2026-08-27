import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory, UserFactory


def _details_url(team, experiment, version_number, compare_to=None):
    url = reverse("chatbots:version-details", args=[team.slug, experiment.id, version_number])
    return f"{url}?compare_to={compare_to}" if compare_to else url


def _three_versions(team, owner):
    """A chatbot with v1, v2 and v3, each renamed so every pair differs."""
    experiment = ExperimentFactory.create(team=team, name="One", owner=owner)
    experiment.create_new_version()
    experiment.name = "Two"
    experiment.save()
    experiment.create_new_version()
    experiment.name = "Three"
    experiment.save()
    experiment.create_new_version()
    return experiment


def _changed_field_names(version_details):
    """Names of every field marked changed, walking nested version details."""
    names = []

    def walk(details):
        for field in details.fields:
            if field.changed:
                names.append(field.name)
            nested = [field.raw_value_version, *(r.raw_value_version for r in field.queryset_results or [])]
            for child in filter(None, nested):
                walk(child)

    walk(version_details)
    return names


@pytest.fixture()
def viewer(team_with_users):
    user = team_with_users.members.first()
    user.user_permissions.add(Permission.objects.get(codename="view_experiment"))
    return user


@pytest.mark.django_db()
class TestVersionDetailsDiff:
    def test_diffs_against_the_previous_version(self, client, team_with_users, viewer):
        """A version's details compare against its predecessor, so changed fields render as changed."""
        client.force_login(viewer)
        experiment = ExperimentFactory.create(team=team_with_users, name="Original", owner=viewer)
        experiment.create_new_version()
        experiment.name = "Renamed"
        experiment.save()
        second = experiment.create_new_version()

        response = client.get(_details_url(team_with_users, experiment, second.version_number))

        assert response.status_code == 200
        details = response.context["version_details"]
        assert details.fields_changed is True
        assert "name" in _changed_field_names(details)

    def test_unchanged_version_reports_no_changes(self, client, team_with_users, viewer):
        client.force_login(viewer)
        experiment = ExperimentFactory.create(team=team_with_users, name="Original", owner=viewer)
        experiment.create_new_version()
        second = experiment.create_new_version()

        response = client.get(_details_url(team_with_users, experiment, second.version_number))

        assert response.status_code == 200
        assert response.context["version_details"].fields_changed is False

    def test_first_version_has_no_predecessor_to_diff(self, client, team_with_users, viewer):
        """v1 has nothing before it, so the page renders without a diff rather than erroring."""
        client.force_login(viewer)
        experiment = ExperimentFactory.create(team=team_with_users, name="Original", owner=viewer)
        first = experiment.create_new_version()

        response = client.get(_details_url(team_with_users, experiment, first.version_number))

        assert response.status_code == 200
        assert response.context["version_details"].fields_changed is False

    def test_archived_predecessor_still_diffs(self, client, team_with_users, viewer):
        """The view shows archived versions, so an archived predecessor must not break the diff."""
        client.force_login(viewer)
        experiment = ExperimentFactory.create(team=team_with_users, name="Original", owner=viewer)
        first = experiment.create_new_version()
        experiment.name = "Renamed"
        experiment.save()
        second = experiment.create_new_version()
        first.is_archived = True
        first.save()

        response = client.get(_details_url(team_with_users, experiment, second.version_number))

        assert response.status_code == 200
        assert "name" in _changed_field_names(response.context["version_details"])

    def test_other_team_cannot_view(self, client, team_with_users, viewer):
        experiment = ExperimentFactory.create(team=team_with_users, name="Original", owner=viewer)
        version = experiment.create_new_version()

        outsider = UserFactory()
        other_team = TeamFactory()
        MembershipFactory.create(user=outsider, team=other_team)
        outsider.user_permissions.add(Permission.objects.get(codename="view_experiment"))
        client.force_login(outsider)

        response = client.get(_details_url(other_team, experiment, version.version_number))

        assert response.status_code == 404


@pytest.mark.django_db()
class TestVersionDetailsComparisonTarget:
    def test_compares_against_an_explicitly_chosen_version(self, client, team_with_users, viewer):
        """`compare_to` overrides the default predecessor, so any two versions can be compared."""
        client.force_login(viewer)
        experiment = _three_versions(team_with_users, viewer)

        response = client.get(_details_url(team_with_users, experiment, 3, compare_to=1))

        assert response.status_code == 200
        assert response.context["compare_to"].version_number == 1
        assert "name" in _changed_field_names(response.context["version_details"])

    def test_defaults_to_the_predecessor(self, client, team_with_users, viewer):
        client.force_login(viewer)
        experiment = _three_versions(team_with_users, viewer)

        response = client.get(_details_url(team_with_users, experiment, 3))

        assert response.context["compare_to"].version_number == 2

    def test_offers_the_other_versions_for_comparison(self, client, team_with_users, viewer):
        client.force_login(viewer)
        experiment = _three_versions(team_with_users, viewer)

        response = client.get(_details_url(team_with_users, experiment, 3))

        offered = [v.version_number for v in response.context["comparison_versions"]]
        assert offered == [2, 1]

    def test_unknown_comparison_target_is_not_found(self, client, team_with_users, viewer):
        client.force_login(viewer)
        experiment = _three_versions(team_with_users, viewer)

        response = client.get(_details_url(team_with_users, experiment, 3, compare_to=99))

        assert response.status_code == 404

    def test_comparing_a_version_with_itself_shows_no_changes(self, client, team_with_users, viewer):
        client.force_login(viewer)
        experiment = _three_versions(team_with_users, viewer)

        response = client.get(_details_url(team_with_users, experiment, 3, compare_to=3))

        assert response.status_code == 200
        assert response.context["version_details"].fields_changed is False
