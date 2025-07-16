import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.chat.models import ChatMessage, ChatMessageType

from ...utils.factories.team import MembershipFactory, TeamFactory
from ..models import DashboardFilter

User = get_user_model()


@pytest.mark.django_db()
class TestDashboardApiViews:
    """Test dashboard API endpoints"""

    def test_overview_stats_api(self, authenticated_client, team, experiment, participant, experiment_session, chat):
        """Test overview statistics API endpoint"""
        # Create some test data
        ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Test message")

        url = reverse("dashboard:api_overview", kwargs={"team_slug": team.slug})
        response = authenticated_client.get(url)

        assert response.status_code == 200
        data = response.json()

        # Check that expected fields are present
        expected_fields = [
            "total_experiments",
            "total_participants",
            "total_sessions",
            "total_messages",
            "active_experiments",
            "active_participants",
        ]
        for field in expected_fields:
            assert field in data
            assert isinstance(data[field], int | float)

    def test_session_analytics_api(self, authenticated_client, team, experiment, participant, experiment_session):
        """Test session analytics API endpoint"""
        url = reverse("dashboard:api_session_analytics", kwargs={"team_slug": team.slug})
        response = authenticated_client.get(url)

        assert response.status_code == 200
        data = response.json()

        assert isinstance(data, dict)
        assert "sessions" in data
        assert "participants" in data

    def test_message_volume_api(self, authenticated_client, team):
        """Test message volume API endpoint"""
        url = reverse("dashboard:api_message_volume", kwargs={"team_slug": team.slug})
        response = authenticated_client.get(url)

        assert response.status_code == 200
        data = response.json()

        assert isinstance(data, dict)
        expected_keys = ["human_messages", "ai_messages", "totals"]
        for key in expected_keys:
            assert key in data
            assert isinstance(data[key], list)

    def test_bot_performance_api(self, authenticated_client, team, experiment):
        """Test bot performance API endpoint"""
        url = reverse("dashboard:api_bot_performance", kwargs={"team_slug": team.slug})
        response = authenticated_client.get(url, kwargs={"team_slug": team.slug})

        assert response.status_code == 200
        data = response.json()

        assert isinstance(data["results"], list)
        if data:  # If there's data
            item = data["results"][0]
            expected_fields = [
                "experiment_id",
                "experiment_name",
                "participants",
                "sessions",
                "messages",
                "completion_rate",
            ]
            for field in expected_fields:
                assert field in item

    def test_channel_breakdown_api(self, authenticated_client, team):
        """Test channel breakdown API endpoint"""
        url = reverse("dashboard:api_channel_breakdown", kwargs={"team_slug": team.slug})
        response = authenticated_client.get(url)

        assert response.status_code == 200
        data = response.json()

        assert isinstance(data, dict)
        assert "platforms" in data
        assert "totals" in data
        assert isinstance(data["platforms"], list)
        assert isinstance(data["totals"], dict)


@pytest.mark.django_db()
class TestFilterManagement:
    """Test filter management functionality"""

    def test_save_filter(self, authenticated_client, team, user):
        """Test saving filter presets"""
        url = reverse("dashboard:save_filter", kwargs={"team_slug": team.slug})

        filter_data = {"date_range": "30", "granularity": "daily", "experiments": [1, 2]}

        data = {"name": "Test Filter", "is_default": True, "filter_data": json.dumps(filter_data)}

        response = authenticated_client.post(url, data)

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["success"] is True

        # Check that filter was saved
        saved_filter = DashboardFilter.objects.get(team=team, user=user, filter_name="Test Filter")
        assert saved_filter.filter_data == filter_data
        assert saved_filter.is_default is True

    def test_load_filter(self, authenticated_client, team, user):
        """Test loading saved filter presets"""
        # Create a saved filter
        filter_data = {"date_range": "7", "experiments": [1]}

        saved_filter = DashboardFilter.objects.create(
            team=team, user=user, filter_name="Test Load Filter", filter_data=filter_data, is_default=False
        )

        url = reverse("dashboard:load_filter", kwargs={"team_slug": team.slug, "filter_id": saved_filter.id})
        response = authenticated_client.get(url)

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["success"] is True
        assert response_data["filter_data"] == filter_data

    def test_load_nonexistent_filter(self, authenticated_client, team):
        """Test loading non-existent filter"""
        url = reverse("dashboard:load_filter", kwargs={"team_slug": team.slug, "filter_id": 99999})
        response = authenticated_client.get(url)

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["success"] is False
        assert "error" in response_data


@pytest.mark.django_db()
class TestDashboardSecurity:
    """Test dashboard security and access controls"""

    def test_team_isolation(self, client):
        """Test that users can only access their team's data"""
        # Create two teams with users
        team1 = TeamFactory()
        team2 = TeamFactory()

        user1 = MembershipFactory(team=team1).user
        user2 = MembershipFactory(team=team2).user

        # Create filter for team1/user1
        filter_data = {"test": "data"}
        saved_filter = DashboardFilter.objects.create(
            team=team1, user=user1, filter_name="Team 1 Filter", filter_data=filter_data
        )

        # Login as user2 (team2)
        client.force_login(user2)

        # Try to access team1's filter
        url = reverse("dashboard:load_filter", kwargs={"team_slug": team1.slug, "filter_id": saved_filter.id})
        response = client.get(url)

        # Should not be able to access other team's filter
        assert response.status_code == 404

    def test_unauthenticated_api_access(self, client, team):
        """Test that API endpoints require authentication"""
        api_endpoints = [
            "dashboard:api_overview",
            "dashboard:api_session_analytics",
            "dashboard:api_message_volume",
            "dashboard:api_bot_performance",
            "dashboard:api_user_engagement",
            "dashboard:api_channel_breakdown",
            "dashboard:api_tag_analytics",
        ]

        for endpoint_name in api_endpoints:
            url = reverse(endpoint_name, kwargs={"team_slug": team.slug})
            response = client.get(url)

            # Should redirect to login or return 403
            assert response.status_code in [302, 403]
