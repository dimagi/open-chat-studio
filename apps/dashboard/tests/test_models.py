from datetime import timedelta

import pytest
from django.utils import timezone

from ..filter_format import convert_saved_filter_data
from ..models import DashboardCache, DashboardFilter


@pytest.mark.django_db()
class TestDashboardCache:
    """Test dashboard cache functionality"""

    def test_cache_data_storage_and_retrieval(self, team):
        """Test basic cache storage and retrieval"""
        cache_key = "test_key"
        test_data = {"metric": "value", "count": 123}

        # Store data
        DashboardCache.set_cached_data(team, cache_key, test_data, ttl_minutes=10)

        # Retrieve data
        retrieved_data = DashboardCache.get_cached_data(team, cache_key)

        assert retrieved_data == test_data

    def test_cache_expiry(self, team):
        """Test cache expiry functionality"""
        cache_key = "expire_test"
        test_data = {"expires": True}

        # Store data with very short TTL
        cache_entry = DashboardCache.set_cached_data(team, cache_key, test_data, ttl_minutes=0)

        # Manually set expiry to past
        cache_entry.expires_at = timezone.now() - timedelta(minutes=1)
        cache_entry.save()

        # Should return None for expired data
        retrieved_data = DashboardCache.get_cached_data(team, cache_key)
        assert retrieved_data is None

    def test_cache_key_uniqueness_per_team(self, team, experiment_team):
        """Test that cache keys are unique per team"""
        cache_key = "same_key"
        team1_data = {"team": "team1"}
        team2_data = {"team": "team2"}

        # Store same key for different teams
        DashboardCache.set_cached_data(team, cache_key, team1_data)
        DashboardCache.set_cached_data(experiment_team, cache_key, team2_data)

        # Retrieve should return team-specific data
        team1_retrieved = DashboardCache.get_cached_data(team, cache_key)
        team2_retrieved = DashboardCache.get_cached_data(experiment_team, cache_key)

        assert team1_retrieved == team1_data
        assert team2_retrieved == team2_data


@pytest.mark.django_db()
class TestDashboardFilter:
    """Test dashboard filter functionality"""

    def test_filter_creation(self, team, user):
        """Test creating filter presets"""
        filter_data = {"date_range": "30", "experiments": [1, 2, 3], "granularity": "daily"}

        dashboard_filter = DashboardFilter.objects.create(
            team=team, user=user, filter_name="Test Filter", filter_data=filter_data, is_default=False
        )

        assert dashboard_filter.filter_name == "Test Filter"
        assert dashboard_filter.filter_data == filter_data
        assert dashboard_filter.team == team
        assert dashboard_filter.user == user

    def test_convert_saved_filter_data_to_new_format(self):
        """Legacy filter payloads should be converted to the new f_/op_ query-style format."""
        legacy_filter_data = {
            "filter_0_column": "status",
            "filter_0_operator": "equals",
            "filter_0_value": "active",
            "filter_1_column": "tags",
            "filter_1_operator": "any of",
            "filter_1_value": '["tag1", "tag2"]',
        }

        converted = convert_saved_filter_data(legacy_filter_data)

        assert converted == {
            "f_status": "active",
            "op_status": "equals",
            "f_tags": "tag1~tag2",
            "op_tags": "any of",
        }
