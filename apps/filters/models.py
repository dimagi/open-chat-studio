from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.teams.models import BaseTeamModel


class FilterSet(BaseTeamModel):
    class TableType(models.TextChoices):
        """
        Defines the allowed table types for filters.
        Always use these constants when setting df_table_type in views.
        """

        SESSIONS = "sessions"
        DATASETS = "datasets"
        ALL_SESSIONS = "all_sessions"
        PARTICIPANTS = "participants"
        TRACES = "traces"

    name = models.CharField(max_length=256)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    table_type = models.CharField(max_length=50, choices=TableType.choices)
    filter_query_string = models.TextField(blank=False)
    is_shared = models.BooleanField(default=False)
    is_starred = models.BooleanField(default=False)
    is_default_for_user = models.BooleanField(default=False)
    is_default_for_team = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["team", "table_type", "user"]),
            models.Index(fields=["team", "table_type", "is_default_for_team"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "user", "table_type"],
                condition=Q(is_default_for_user=True),
                name="unique_default_filterset_per_user_table_type",
            ),
            models.UniqueConstraint(
                fields=["team", "table_type"],
                condition=Q(is_default_for_team=True),
                name="unique_default_filterset_per_team_table_type",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.table_type})"

    @classmethod
    def is_valid_table_type(cls, table_type: str) -> bool:
        """Check if a given table_type is valid."""
        return table_type in dict(cls.TableType.choices)
