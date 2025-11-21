from django.db import models


class CustomMigration(models.Model):
    """
    Database table to track applied custom migrations.

    This model allows tracking of custom data migrations that run outside
    the standard Django migration framework, ensuring they run exactly once
    across all environments.
    """

    name = models.CharField(max_length=255, unique=True, help_text="Unique identifier for the migration")
    applied_at = models.DateTimeField(auto_now_add=True, help_text="Timestamp when migration was applied")

    class Meta:
        db_table = "custom_migrations"
        ordering = ["-applied_at"]

    def __str__(self):
        return f"{self.name} (applied at {self.applied_at})"
