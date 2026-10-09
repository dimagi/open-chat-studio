import hashlib

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.ocs_notifications.models import EventUser, LevelChoices, UserNotificationPreferences
from apps.ocs_notifications.utils import (
    ALL_TEAMS_CACHE_KEY,
    get_user_notification_cache_value,
    set_user_notification_cache,
    set_user_notification_cache_many,
)
from apps.teams.models import Team
from apps.users.model_audit_fields import CUSTOM_USER_FIELDS
from apps.web.storage_backends import get_public_media_storage


class AuditedUserObjectManager(UserManager, AuditingManager):
    # Disable this to work around account.0006_emailaddress_lower migration
    # which does a data migration using the user model but does not pass the `audit_action` keyword.
    use_in_migrations = False


@audit_fields(*CUSTOM_USER_FIELDS, audit_special_queryset_writes=True)
class CustomUser(AbstractUser):
    """
    Add additional fields to the user model here.
    """

    objects = AuditedUserObjectManager()
    migration_objects = UserManager()
    avatar = models.FileField(upload_to="profile-pictures/", blank=True, storage=get_public_media_storage)
    language = models.CharField(max_length=10, blank=True, null=True)  # noqa DJ001

    class Meta:
        # Set the base manager to the default UserManager to avoid issues with the auditing queryset in
        # migrations etc e.g. 3rd party apps that do data migrations won't include the audit_action keyword.
        base_manager_name = "migration_objects"

    def __str__(self):
        return f"{self.get_full_name()} <{self.email or self.username}>"

    def get_display_name(self) -> str:
        if self.get_full_name().strip():
            return self.get_full_name()
        return self.email or self.username

    @property
    def avatar_url(self) -> str:
        if self.avatar:
            return self.avatar.url
        else:
            return f"https://www.gravatar.com/avatar/{self.gravatar_id}?s=128&d=identicon"

    @property
    def gravatar_id(self) -> str:
        # https://en.gravatar.com/site/implement/hash/
        return hashlib.md5(self.email.lower().strip().encode("utf-8")).hexdigest()

    def unread_notifications_count(self, team: Team) -> int:
        """
        Get the count of unread notifications for the user.

        Returns the number of unread in-app notifications based on the user's
        notification preferences. The count is cached to improve performance and
        reduces database queries on repeated calls.

        Returns:
            int: The number of unread notifications for this user.
        """
        count = get_user_notification_cache_value(self.id, team_slug=team.slug)
        if count is not None:
            return count

        preferences = UserNotificationPreferences.objects.filter(user=self, team=team).first()
        in_app_enabled = preferences.in_app_enabled if preferences else True
        level = preferences.in_app_level if preferences else LevelChoices.INFO
        if in_app_enabled:
            count = EventUser.objects.filter(
                team__slug=team.slug,
                user_id=self.id,
                read=False,
                event_type__level__gte=level,
            ).count()
        else:
            count = 0

        # This cache gets busted when an error happens or when the user changes preferences
        set_user_notification_cache(self.id, team_slug=team.slug, count=count)
        return count

    def unread_notifications_count_all_teams(self) -> int:
        """
        Get the count of unread notifications for the user across every team they belong to.

        On a cache miss, computes every team's count with a fixed number of queries (teams,
        preferences, and one grouped unread count) rather than querying per team, then caches
        both the per-team counts and the aggregate.

        Returns:
            int: The number of unread notifications for this user across all their teams.
        """
        count = get_user_notification_cache_value(self.id, team_slug=ALL_TEAMS_CACHE_KEY)
        if count is not None:
            return count

        team_slugs = dict(self.teams.values_list("id", "slug"))
        if not team_slugs:
            set_user_notification_cache(self.id, team_slug=ALL_TEAMS_CACHE_KEY, count=0)
            return 0

        # One query for every team's preferences. Ordered by id so the first row per team wins,
        # matching `.first()` in `unread_notifications_count`.
        thresholds = {}
        preferences = (
            UserNotificationPreferences.objects.filter(user=self, team_id__in=team_slugs)
            .order_by("id")
            .values_list("team_id", "in_app_enabled", "in_app_level")
        )
        for team_id, in_app_enabled, in_app_level in preferences:
            thresholds.setdefault(team_id, in_app_level if in_app_enabled else None)

        # One query for unread counts across all teams, grouped so per-team levels can be applied in Python.
        unread_rows = (
            EventUser.objects.filter(user_id=self.id, read=False, team_id__in=team_slugs)
            .values("team_id", "event_type__level")
            .annotate(unread=models.Count("id"))
            .order_by()
        )
        counts_by_team_id = dict.fromkeys(team_slugs, 0)
        for row in unread_rows:
            team_id = row["team_id"]
            threshold = thresholds.get(team_id, LevelChoices.INFO)
            if threshold is not None and row["event_type__level"] >= threshold:
                counts_by_team_id[team_id] += row["unread"]

        set_user_notification_cache_many(
            self.id, {team_slugs[team_id]: count for team_id, count in counts_by_team_id.items()}
        )
        count = sum(counts_by_team_id.values())

        set_user_notification_cache(self.id, team_slug=ALL_TEAMS_CACHE_KEY, count=count)
        return count
