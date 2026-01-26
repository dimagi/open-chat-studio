import pytest

from apps.utils.factories.custom_actions import ACTION_SCHEMA, CustomActionFactory


@pytest.mark.django_db()
class TestCustomActionModel:
    """Tests for CustomAction model health fields."""

    def test_default_health_status(self, team_with_users):
        """Test that new actions have default health status of 'unknown'."""
        action = CustomActionFactory(team=team_with_users)
        assert action.health_status == "unknown"
        assert action.last_health_check is None

    def test_detect_health_endpoint_from_spec(self, team_with_users):
        """Test that health endpoint can be detected from API spec."""

        # Add a health endpoint to the schema
        schema_with_health = {
            **ACTION_SCHEMA,
            "paths": {
                **ACTION_SCHEMA["paths"],
                "/health": {
                    "get": {
                        "summary": "Health check",
                    }
                },
            },
        }

        action = CustomActionFactory(
            team=team_with_users, api_schema=schema_with_health, server_url="https://api.example.com"
        )

        detected = action.detect_health_endpoint_from_spec()
        assert detected == "/health"

    def test_detect_health_endpoint_multiple_patterns(self, team_with_users):
        """Test detection of various health endpoint patterns."""

        test_cases = [
            ("/health", "/health"),
            ("/healthz", "/healthz"),
            ("/api/health", "/api/health"),
            ("/status", "/status"),
        ]

        for path, expected_url in test_cases:
            schema = {
                **ACTION_SCHEMA,
                "paths": {
                    path: {
                        "get": {
                            "summary": "Health check",
                        }
                    },
                },
            }

            action = CustomActionFactory(team=team_with_users, api_schema=schema, server_url="https://api.example.com")

            detected = action.detect_health_endpoint_from_spec()
            assert detected == expected_url

    def test_detect_health_endpoint_no_match(self, team_with_users):
        """Test that None is returned when no health endpoint is found."""
        action = CustomActionFactory(team=team_with_users)

        detected = action.detect_health_endpoint_from_spec()
        assert detected is None

    @pytest.mark.parametrize(
        ("server_url", "healthcheck_path", "expected"),
        [
            ("https://api.example.com", "/health", "https://api.example.com/health"),
            ("https://api.example.com", "/api/v1/health", "https://api.example.com/api/v1/health"),
            ("https://api.example.com/", "health/", "https://api.example.com/health/"),
            ("https://api.example.com/", "/health/", "https://api.example.com/health/"),
            ("https://api.example.com", "", None),
        ],
        ids=[
            "simple_health_path",
            "nested_path",
            "trailing_slash",
            "leading_and_trailing_slash",
            "empty_path",
        ],
    )
    def test_health_endpoint_property(self, team_with_users, server_url, healthcheck_path, expected):
        """Test that health_endpoint property correctly combines server URL and healthcheck path."""
        action = CustomActionFactory(team=team_with_users, server_url=server_url, healthcheck_path=healthcheck_path)
        assert action.health_endpoint == expected
