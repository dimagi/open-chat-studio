from unittest.mock import Mock, patch

import pytest

from apps.mcp_integrations.models import McpServer
from apps.utils.factories.service_provider_factories import AuthProviderFactory


@pytest.mark.django_db()
class TestMcpServer:
    def test_sync_tools(self, team):
        server = McpServer.objects.create(
            team=team,
            name="Test Server",
            server_url="http://example.com/mcp",
        )

        tool1 = Mock()
        tool1.name = "Tool1"
        tool2 = Mock()
        tool2.name = "T" * 300  # Exceeds max length for CharField, but this will be truncated
        with patch("apps.mcp_integrations.models.McpServer._fetch_tools_from_mcp_server") as mock_fetch:
            mock_fetch.return_value = [tool1, tool2]
            server.sync_tools()

        server.refresh_from_db()
        assert server.available_tools == ["Tool1", "T" * 255]  # Should truncate to max length
        assert server.tool_count > 0

    def test_fetch_tools_with_auth(self, team):
        auth_provider = AuthProviderFactory(team=team)
        auth_service_mock = Mock()
        auth_service_mock.get_auth_headers.return_value = {"Authorization": "Bearer token"}

        server = McpServer.objects.create(
            team=team, name="Test Server", server_url="http://example.com/mcp", auth_provider=auth_provider
        )

        with patch.object(auth_provider, "get_auth_service", return_value=auth_service_mock):
            with patch("apps.mcp_integrations.models.McpServer._fetch_tools_from_mcp_server") as mock_fetch:
                mock_fetch.return_value = []
                server.fetch_tools()
                mock_fetch.assert_called_once_with({"Authorization": "Bearer token"})
