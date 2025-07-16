from unittest.mock import Mock

import pytest

from apps.banners.services import get_dismissed_banner_ids


class TestGetDismissedBannerIds:
    def test_no_cookies(self):
        """Test when request has no cookies"""
        request = Mock()
        request.COOKIES = {}

        result = get_dismissed_banner_ids(request)
        assert result == []

    def test_no_matching_cookies(self):
        """Test when cookies exist but none match the pattern"""
        request = Mock()
        request.COOKIES = {"sessionid": "abc123", "csrf_token": "xyz789", "user_pref": "dark_mode"}

        result = get_dismissed_banner_ids(request)
        assert result == []

    def test_single_matching_cookie(self):
        """Test with one matching cookie"""
        request = Mock()
        request.COOKIES = {"dismissed_banner_123": "true", "other_cookie": "value"}

        result = get_dismissed_banner_ids(request)
        assert result == [123]

    def test_multiple_matching_cookies(self):
        """Test with multiple matching cookies"""
        request = Mock()
        request.COOKIES = {
            "dismissed_banner_1": "true",
            "dismissed_banner_42": "true",
            "dismissed_banner_999": "true",
            "other_cookie": "value",
        }

        result = get_dismissed_banner_ids(request)
        assert set(result) == {1, 42, 999}
        assert len(result) == 3

    def test_empty_cookie_values(self):
        """Test that function works regardless of cookie values"""
        request = Mock()
        request.COOKIES = {"dismissed_banner_1": "", "dismissed_banner_2": None, "dismissed_banner_3": "some_value"}

        result = get_dismissed_banner_ids(request)
        assert set(result) == {1, 2, 3}

    def test_realistic_cookie_scenario(self):
        """Test with realistic cookie scenario"""
        request = Mock()
        request.COOKIES = {
            "sessionid": "a1b2c3d4e5f6",
            "csrf_token": "token123",
            "dismissed_banner_1": "true",
            "dismissed_banner_15": "true",
            "dismissed_banner_42": "true",
            "user_preferences": "json_encoded_prefs",
            "last_visit": "2024-01-01",
            "dismissed_banner_100": "true",
        }

        result = get_dismissed_banner_ids(request)
        assert set(result) == {1, 15, 42, 100}
        assert len(result) == 4

    @pytest.mark.parametrize(
        ("cookie_name", "expected_id"),
        [
            ("dismissed_banner_123", 123),
            ("dismissed_banner_0", 0),
            ("dismissed_banner_999999", 999999),
        ],
    )
    def test_valid_cookie_patterns(self, cookie_name, expected_id):
        """Parametrized test for valid cookie patterns"""
        request = Mock()
        request.COOKIES = {cookie_name: "true"}

        result = get_dismissed_banner_ids(request)
        assert result == [expected_id]

    @pytest.mark.parametrize(
        "cookie_name",
        [
            "banner_123",  # Missing 'dismissed_' prefix
            "dismissed_123",  # Missing 'banner_'
            "dismissed_banner_",  # Missing ID
            "dismissed_banner_abc",  # Non-numeric ID
            "dismissed_banner_12.3",  # Decimal
            "dismissed_banner_12a",  # Mixed alphanumeric
            "dismissed_banner_-12",  # Negative
            "dismissed_banner_12 ",  # extra suffix
            "_dismissed_banner_12",  # extra prefix
        ],
    )
    def test_invalid_cookie_patterns(self, cookie_name):
        """Parametrized test for invalid cookie patterns"""
        request = Mock()
        request.COOKIES = {cookie_name: "true"}

        result = get_dismissed_banner_ids(request)
        assert result == []
