from unittest.mock import Mock, patch

import pytest
from django.conf import settings

from apps.mcp_integrations.forms import McpServerForm
from apps.utils.urlvalidate import InvalidURL


@pytest.mark.django_db()
class TestMcpServerForm:
    def test_invalid_url_raises_validation_error(self, team):
        """Test that InvalidURL exception is converted to ValidationError"""
        request = Mock(team=team)
        form = McpServerForm(
            request=request,
            data={
                "name": "Test Server",
                "server_url": "https://unsafe-ip.example.com/mcp",
                "transport_type": "streamable_http",
            },
        )

        # Mock validate_user_input_url to raise InvalidURL exception
        with patch("apps.mcp_integrations.forms.validate_user_input_url") as mock_validate:
            mock_validate.side_effect = InvalidURL("Unsafe IP address: 192.168.1.1")

            # Form should be invalid
            assert form.is_valid() is False

            # Should have a ValidationError on the server_url field
            assert "server_url" in form.errors
            assert "The server URL is invalid: Unsafe IP address: 192.168.1.1" in form.errors["server_url"][0]

            # Verify the mock was called
            mock_validate.assert_called_once_with("https://unsafe-ip.example.com/mcp", strict=not settings.DEBUG)
