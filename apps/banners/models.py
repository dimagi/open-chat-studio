from django.db import models
from django.utils import timezone


class Banner(models.Model):
    """Model to store temporary banner notifications."""

    BANNER_TYPES = (
        ("info", "Information"),
        ("warning", "Warning"),
        ("danger", "Danger"),
        ("success", "Success"),
    )
    # Pre-defined locations must match those in the middleware.py
    LOCATIONS = (
        ("global", "Global (All Pages)"),
        ("experiments_home", "Experiments Home Page"),
        ("experiments_new", "New Experiments Page"),
        ("pipelines", "Pipelines Home Page"),
        ("pipelines_new", "New Pipelines Page"),
        ("chatbots_home", "Chatbots Home Page"),
        ("chatbots_new", "New Chatbot Page"),
        ("team_settings", "Team Settings Page"),
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
