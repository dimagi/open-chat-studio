from django.db import models
from django.utils import timezone


class Banner(models.Model):
    """Model to store temporary banner notifications."""

    BANNER_TYPES = (
        ("info", "Information"),
        ("warning", "Warning"),
        ("error", "Error"),
        ("success", "Success"),
    )
    # Pre-defined locations must match those in the middleware.py
    LOCATIONS = (
        ("global", "Global (All Pages)"),
        ("experiments_home", "Experiments Home"),
        ("experiments_new", "New Experiments"),
        ("pipelines", "Pipelines Home"),
        ("pipelines_new", "New Pipelines"),
        ("chatbots_home", "Chatbots Home"),
        ("chatbots_new", "New Chatbot"),
        ("assistants_home", "Assistants Home"),
        ("team_settings", "Team Settings"),
    )

    title = models.CharField(max_length=100, blank=True, help_text="Optional title for the banner")
    message = models.TextField(help_text="Display message rendered as markdown")
    banner_type = models.CharField(
        max_length=20, choices=BANNER_TYPES, default="info", help_text="Visual style of the banner"
    )
    start_date = models.DateTimeField(default=timezone.now, help_text="When this banner should start being displayed")
    end_date = models.DateTimeField(help_text="When this banner should stop being displayed")
    is_active = models.BooleanField(default=True, help_text="Manually enable/disable this banner")
    location = models.CharField(
        max_length=100, choices=LOCATIONS, default="global", help_text="Location of banner on site"
    )
    feature_flag = models.ForeignKey(
        "teams.Flag",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        help_text="Banner will only show if team has the flag enabled",
    )

    class Meta:
        ordering = ["-end_date"]
        verbose_name = "Banner"
        verbose_name_plural = "Banners"

    def __str__(self):
        if self.title:
            return self.title
        return f"Banner {self.id}: {self.message[:30]}..."

    @property
    def is_expired(self):
        return timezone.now() > self.end_date

    @property
    def is_future(self):
        return timezone.now() < self.start_date

    @property
    def is_visible(self):
        now = timezone.now()
        return self.is_active and self.start_date <= now and self.end_date > now
