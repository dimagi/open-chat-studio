import pytest
from django.urls import reverse

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import Experiment


@pytest.mark.django_db()
class TestEngagementDashboardView:
    def test_renders_page(self, authenticated_client, team):
        url = reverse("dashboard:engagement", kwargs={"team_slug": team.slug})

        response = authenticated_client.get(url)

        assert response.status_code == 200
        assert response.templates[0].name == "dashboard/engagement.html"

    def test_requires_login(self, team, client):
        url = reverse("dashboard:engagement", kwargs={"team_slug": team.slug})

        response = client.get(url)

        assert response.status_code in (302, 403)


@pytest.mark.django_db()
class TestEngagementApiViews:
    def test_summary_api(self, authenticated_client, team, experiment, participant, experiment_session, chat):
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="hi")
        url = reverse("dashboard:api_engagement_summary", kwargs={"team_slug": team.slug})

        response = authenticated_client.get(url)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert data[-1]["in_progress"] is True

    def test_frequency_api(self, authenticated_client, team):
        url = reverse("dashboard:api_engagement_frequency", kwargs={"team_slug": team.slug})

        response = authenticated_client.get(url)

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_new_vs_returning_api(self, authenticated_client, team):
        url = reverse("dashboard:api_new_vs_returning", kwargs={"team_slug": team.slug})

        response = authenticated_client.get(url)

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_average_session_duration_api(self, authenticated_client, team):
        url = reverse("dashboard:api_average_session_duration", kwargs={"team_slug": team.slug})

        response = authenticated_client.get(url)

        assert response.status_code == 200
        assert isinstance(response.json(), float)

    def test_apis_respect_chatbot_filter(self, authenticated_client, team, experiment, participant, chat, user):
        # An invalid experiment PK would fail form validation and silently skip filtering entirely.
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="hi")
        other_experiment = Experiment.objects.create(name="Other bot", description="Other bot", team=team, owner=user)
        url = reverse("dashboard:api_engagement_summary", kwargs={"team_slug": team.slug})

        response = authenticated_client.get(url, {"experiments": [other_experiment.id]})

        assert response.status_code == 200
        assert response.json()[-1]["mau"] == 0
