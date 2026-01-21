import logging

from django.db import models
from django.template import engines
from django.utils import timezone

logger = logging.getLogger("ocs.banners")


class Banner(models.Model):
    """Model to store temporary banner notifications."""

    BANNER_TYPES = (
        ("info", "Information"),
        ("warning", "Warning"),
        ("error", "Error"),
        ("success", "Success"),
    )
    # Pre-defined locations must match those in the views.py
    LOCATIONS = (
        ("global", "Global (All Pages)"),
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
    dismiss_timeout = models.PositiveSmallIntegerField(
        default=0, help_text="The banner will re-appear this many days after being dismissed"
    )
    site = models.ForeignKey(
        "sites.Site",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The site where the banner will be displayed. Leave blank for all sites.",
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

    def get_formatted_message(self, request):
        try:
            django_engine = engines["django"]
            template = django_engine.from_string(self.message)
            return template.render({"request": request})
        except Exception as e:
            logger.exception("Error rendering banner")
            if request.user.is_superuser:
                return f"ERROR: {str(e)}"

        return ""

    @property
    def cookie_expires(self):
        """The cookie should expire when the banner expires
        or at after `dismiss_timeout` days, whichever is sooner."""
        expires_delta = self.end_date - timezone.now()
        delta_days = expires_delta.days + 1  # approximate
        if not self.dismiss_timeout:
            return delta_days
        return min(delta_days, self.dismiss_timeout)
