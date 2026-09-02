import pytest
from django.urls import reverse

from apps.ocs_notifications.models import NotificationChannel
from apps.service_providers.models import MessagingProviderType
from apps.teams.backends import TEAM_ADMIN_GROUP, add_user_to_team, create_default_groups
from apps.utils.factories.notifications import NotificationChannelFactory, SlackMessagingProviderFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def admin_client(client):
    create_default_groups()
    team = TeamFactory.create()
    user = UserFactory.create()
    add_user_to_team(team, user, groups=[TEAM_ADMIN_GROUP])
    client.force_login(user)
    return client, team


@pytest.mark.django_db()
class TestNotificationChannelViews:
    def test_home(self, admin_client):
        client, team = admin_client
        response = client.get(reverse("ocs_notifications_channels:home", args=[team.slug]))
        assert response.status_code == 200

    def test_table_lists_only_teams_channels(self, admin_client):
        client, team = admin_client
        NotificationChannelFactory.create_batch(3, team=team)
        other_team = TeamFactory.create()
        NotificationChannelFactory.create_batch(2, team=other_team)

        response = client.get(reverse("ocs_notifications_channels:table", args=[team.slug]))

        assert response.status_code == 200
        assert len(response.context["table"].rows) == 3

    def test_create_view(self, admin_client):
        client, team = admin_client
        response = client.get(reverse("ocs_notifications_channels:new", args=[team.slug]))
        assert response.status_code == 200

    def test_edit_view(self, admin_client):
        client, team = admin_client
        channel = NotificationChannelFactory.create(team=team)
        response = client.get(reverse("ocs_notifications_channels:edit", args=[team.slug, channel.pk]))
        assert response.status_code == 200

    def test_delete_view(self, admin_client):
        client, team = admin_client
        channel = NotificationChannelFactory.create(team=team)
        response = client.delete(reverse("ocs_notifications_channels:delete", args=[team.slug, channel.pk]))
        assert response.status_code == 200
        assert not NotificationChannel.objects.filter(pk=channel.pk).exists()

    def test_create_saves_channel_for_team(self, admin_client):
        client, team = admin_client
        provider = SlackMessagingProviderFactory.create(team=team)

        response = client.post(
            reverse("ocs_notifications_channels:new", args=[team.slug]),
            {
                "messaging_provider": provider.pk,
                "channel_name": "#alerts",
                "level": "1",
                "enabled": "on",
            },
        )

        assert response.status_code == 302
        channel = NotificationChannel.objects.get(team=team)
        assert channel.channel_name == "#alerts"
        assert channel.messaging_provider_id == provider.pk

    def test_create_rejects_non_slack_provider(self, admin_client):
        client, team = admin_client
        provider = MessagingProviderFactory.create(team=team, type=MessagingProviderType.twilio)

        response = client.post(
            reverse("ocs_notifications_channels:new", args=[team.slug]),
            {
                "messaging_provider": provider.pk,
                "channel_name": "#alerts",
                "level": "1",
                "enabled": "on",
            },
        )

        assert response.status_code == 200
        assert any("Select a valid choice" in error for error in response.context["form"].errors["messaging_provider"])
        assert not NotificationChannel.objects.filter(team=team).exists()

    def test_permission_denied_for_non_admin(self, client):
        create_default_groups()
        team = TeamFactory.create()
        user = UserFactory.create()
        add_user_to_team(team, user)
        client.force_login(user)

        response = client.get(reverse("ocs_notifications_channels:table", args=[team.slug]))

        assert response.status_code == 403
