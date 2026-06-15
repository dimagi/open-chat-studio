import pytest
from django.urls import reverse

from apps.experiments.models import Survey
from apps.utils.factories.experiment import SurveyFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
class TestCreateSurveyBlocked:
    def test_get_redirects_to_home(self, client):
        team = TeamWithUsersFactory.create()
        user = team.members.first()
        client.force_login(user)
        url = reverse("experiments:survey_new", args=[team.slug])
        response = client.get(url)
        assert response.status_code == 302
        assert response["Location"] == reverse("experiments:survey_home", args=[team.slug])

    def test_post_does_not_create_survey(self, client):
        team = TeamWithUsersFactory.create()
        user = team.members.first()
        client.force_login(user)
        url = reverse("experiments:survey_new", args=[team.slug])
        count_before = Survey.objects.filter(team=team).count()
        client.post(url, data={"name": "New Survey", "url": "https://example.com/survey"})
        assert Survey.objects.filter(team=team).count() == count_before


@pytest.mark.django_db()
class TestEditSurveyReadOnly:
    def test_get_returns_200_with_disabled_fields(self, client):
        team = TeamWithUsersFactory.create()
        user = team.members.first()
        survey = SurveyFactory.create(team=team)
        client.force_login(user)
        url = reverse("experiments:survey_edit", args=[team.slug, survey.id])
        response = client.get(url)
        assert response.status_code == 200
        form = response.context["form"]
        for field in form.fields.values():
            assert field.disabled is True

    def test_post_does_not_update_survey(self, client):
        team = TeamWithUsersFactory.create()
        user = team.members.first()
        original_name = "Original Survey Name"
        survey = SurveyFactory.create(team=team, name=original_name)
        client.force_login(user)
        url = reverse("experiments:survey_edit", args=[team.slug, survey.id])
        response = client.post(url, data={"name": "Hacked Name", "url": "https://hacked.com"})
        assert response.status_code == 302
        survey.refresh_from_db()
        assert survey.name == original_name


@pytest.mark.django_db()
class TestDeleteSurvey:
    def test_delete_archives_survey(self, client):
        team = TeamWithUsersFactory.create()
        user = team.members.first()
        survey = SurveyFactory.create(team=team)
        client.force_login(user)
        url = reverse("experiments:survey_delete", args=[team.slug, survey.id])
        response = client.delete(url)
        assert response.status_code == 200
        survey.refresh_from_db()
        assert survey.is_archived is True


@pytest.mark.django_db()
class TestSurveyHome:
    def test_home_renders_with_deprecation_warning(self, client):
        team = TeamWithUsersFactory.create()
        user = team.members.first()
        client.force_login(user)
        url = reverse("experiments:survey_home", args=[team.slug])
        response = client.get(url)
        assert response.status_code == 200
        assert b"2026-07-10" in response.content
