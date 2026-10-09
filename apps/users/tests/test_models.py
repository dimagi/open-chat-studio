import pytest

from apps.ocs_notifications.models import LevelChoices
from apps.ocs_notifications.utils import get_user_notification_cache_value
from apps.utils.factories.notifications import (
    EventTypeFactory,
    EventUserFactory,
    UserNotificationPreferencesFactory,
)
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

    def test_respects_per_team_in_app_level(self):
        user = UserFactory.create()
        team = TeamFactory.create()
        MembershipFactory.create(user=user, team=team)
        UserNotificationPreferencesFactory.create(user=user, team=team, in_app_level=LevelChoices.WARNING)

        for level in (LevelChoices.INFO, LevelChoices.WARNING, LevelChoices.ERROR):
            EventUserFactory.create(
                user=user, team=team, event_type=EventTypeFactory.create(team=team, level=level), read=False
            )

        assert user.unread_notifications_count_all_teams() == 2

    def test_ignores_notifications_from_teams_the_user_is_not_in(self):
        user = UserFactory.create()
        team = TeamFactory.create()
        MembershipFactory.create(user=user, team=team)
        EventUserFactory.create(user=user, team=team, read=False)
        EventUserFactory.create(user=user, team=TeamFactory.create(), read=False)

        assert user.unread_notifications_count_all_teams() == 1

    def test_populates_per_team_cache(self):
        user = UserFactory.create()
        team_a = TeamFactory.create()
        team_b = TeamFactory.create()
        MembershipFactory.create(user=user, team=team_a)
        MembershipFactory.create(user=user, team=team_b)
        EventUserFactory.create(user=user, team=team_a, read=False)

        user.unread_notifications_count_all_teams()

        assert get_user_notification_cache_value(user.id, team_slug=team_a.slug) == 1
        assert get_user_notification_cache_value(user.id, team_slug=team_b.slug) == 0

    @pytest.mark.parametrize("num_teams", [pytest.param(1, id="one_team"), pytest.param(5, id="five_teams")])
    def test_query_count_is_constant_in_number_of_teams(self, num_teams, django_assert_num_queries):
        user = UserFactory.create()
        for _ in range(num_teams):
            team = TeamFactory.create()
            MembershipFactory.create(user=user, team=team)
            UserNotificationPreferencesFactory.create(user=user, team=team)
            EventUserFactory.create(user=user, team=team, read=False)

        # teams + preferences + grouped unread counts
        with django_assert_num_queries(3):
            assert user.unread_notifications_count_all_teams() == num_teams
