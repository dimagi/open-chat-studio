import pytest

from apps.custom_actions.forms import CustomActionForm


def _make_openapi_schema(paths: dict):
    """Helper to create a minimal valid OpenAPI schema."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "https://api.weather.com"}],
        "paths": paths,
    }


@pytest.mark.django_db()
class TestCustomActionForm:
    def test_form_saves_health_endpoint(self, rf, team_with_users):
        """Test that the form saves the health_endpoint value."""

        request = rf.get("/")
        request.team = team_with_users

        data = {
            "name": "Test Action",
            "description": "Test description",
            "server_url": "https://api.weather.com",
            "healthcheck_path": "/healthz",
            "api_schema": _make_openapi_schema({"/test": {"get": {}}}),
            "prompt": "Test prompt",
        }

        form = CustomActionForm(request=request, data=data)
        assert form.is_valid(), form.errors

        action = form.save(commit=False)
        action.team = team_with_users
        action.save()

        assert action.healthcheck_path == "/healthz"

    def test_form_auto_detects_health_endpoint(self, rf, team_with_users):
        """Test that the form auto-detects health endpoint from API schema."""

        request = rf.get("/")
        request.team = team_with_users

        # Schema with health endpoint
        schema_with_health = _make_openapi_schema(
            {
                "/test": {"get": {}},
                "/health": {"get": {"summary": "Health check"}},
            }
        )

        data = {
            "name": "Test Action",
            "description": "Test description",
            "server_url": "https://api.weather.com",
            "api_schema": schema_with_health,
            "prompt": "Test prompt",
        }

        form = CustomActionForm(request=request, data=data)
        assert form.is_valid(), form.errors

        action = form.save(commit=False)
        action.team = team_with_users
        action.save()

        # Should have auto-detected the health endpoint
        assert action.healthcheck_path == "/health"

    def test_form_manual_override_auto_detection(self, rf, team_with_users):
        """Test that manual health_endpoint overrides auto-detection."""

        request = rf.get("/")
        request.team = team_with_users

        # Schema with health endpoint
        schema_with_health = _make_openapi_schema(
            {
                "/test": {"get": {}},
                "/health": {"get": {"summary": "Health check"}},
            }
        )

        data = {
            "name": "Test Action",
            "description": "Test description",
            "healthcheck_path": "/status",  # Manual override
            "server_url": "https://api.weather.com",
            "api_schema": schema_with_health,
            "prompt": "Test prompt",
        }

        form = CustomActionForm(request=request, data=data)
        assert form.is_valid(), form.errors

        action = form.save(commit=False)
        action.team = team_with_users
        action.save()

        # Should use the manually provided endpoint, not the auto-detected one
        assert action.healthcheck_path == "/status"
