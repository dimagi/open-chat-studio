import pytest

from apps.utils.factories.notifications import EventUserFactory, UserNotificationPreferencesFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.user import UserFactory


@pytest.mark.django_db()
class TestUnreadNotificationsCountAllTeams:
    """Tests for CustomUser.unread_notifications_count_all_teams()"""

    def test_sums_unread_counts_across_all_of_the_users_teams(self):
        user = UserFactory.create()
        team_a = TeamFactory.create()
        team_b = TeamFactory.create()
        MembershipFactory.create(user=user, team=team_a)
        MembershipFactory.create(user=user, team=team_b)

        EventUserFactory.create(user=user, team=team_a, read=False)
        EventUserFactory.create(user=user, team=team_a, read=False)
        EventUserFactory.create(user=user, team=team_b, read=False)
        EventUserFactory.create(user=user, team=team_b, read=True)

        assert user.unread_notifications_count_all_teams() == 3

    def test_result_is_cached(self):
        user = UserFactory.create()
        team = TeamFactory.create()
        MembershipFactory.create(user=user, team=team)
        EventUserFactory.create(user=user, team=team, read=False)

        assert user.unread_notifications_count_all_teams() == 1

        # A new unread notification created directly in the DB, bypassing cache busting.
        EventUserFactory.create(user=user, team=team, read=False)

        # The stale cached value is returned rather than the fresh (higher) count.
        assert user.unread_notifications_count_all_teams() == 1

    def test_respects_per_team_notification_preferences(self):
        user = UserFactory.create()
        team_a = TeamFactory.create()
        team_b = TeamFactory.create()
        MembershipFactory.create(user=user, team=team_a)
        MembershipFactory.create(user=user, team=team_b)
        UserNotificationPreferencesFactory.create(user=user, team=team_b, in_app_enabled=False)

        EventUserFactory.create(user=user, team=team_a, read=False)
        EventUserFactory.create(user=user, team=team_b, read=False)

        assert user.unread_notifications_count_all_teams() == 1

    def test_zero_when_user_belongs_to_no_teams(self):
        user = UserFactory.create()
        assert user.unread_notifications_count_all_teams() == 0
