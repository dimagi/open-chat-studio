from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.teams.models import BaseTeamModel


class DashboardCache(BaseTeamModel):
    """Cache computed dashboard metrics to improve performance"""

    cache_key = models.CharField(max_length=255)
    data = models.JSONField()
    expires_at = models.DateTimeField()

    class Meta:
        unique_together = ("team", "cache_key")
        indexes = [
            models.Index(fields=["team", "cache_key", "expires_at"]),
        ]

    def __str__(self):
        return self.cache_key

    @classmethod
    def get_cached_data(cls, team, cache_key):
        """Get cached data if not expired"""
        try:
            cache_entry = cls.objects.get(team=team, cache_key=cache_key, expires_at__gt=timezone.now())
            return cache_entry.data
        except cls.DoesNotExist:
            return None

    @classmethod
    def set_cached_data(cls, team, cache_key, data, ttl_minutes=30):
        """Cache data with TTL"""
        expires_at = timezone.now() + timedelta(minutes=ttl_minutes)
        cache_entry, created = cls.objects.update_or_create(
            team=team,
            cache_key=cache_key,
            defaults={
                "data": data,
                "expires_at": expires_at,
            },
        )
        return cache_entry


class DashboardFilter(BaseTeamModel):
    """Store user's dashboard filter preferences"""

    user = models.ForeignKey("users.CustomUser", on_delete=models.CASCADE)
    filter_name = models.CharField(max_length=100)  # e.g., 'date_range', 'experiments', 'channels'
    filter_data = models.JSONField()  # Store filter parameters
    is_default = models.BooleanField(default=False)

    class Meta:
        unique_together = ("team", "user", "filter_name")
        indexes = [
            models.Index(fields=["team", "user", "filter_name"]),
        ]

    def __str__(self):
        return self.filter_name
