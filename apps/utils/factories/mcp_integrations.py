import factory

from apps.mcp_integrations.models import McpServer


class MCPServerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = McpServer

    name = factory.Faker("name")
    server_url = factory.Faker("url")
