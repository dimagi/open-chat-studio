from django.contrib import admin
from django.utils import timezone

from .models import Banner


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = [
        "title_display",
        "message_preview",
        "banner_type",
        "location",
        "start_date",
        "end_date",
        "is_active",
        "status_display",
    ]
    list_filter = ["banner_type", "is_active", "location"]
    search_fields = ["title", "message"]
    date_hierarchy = "end_date"
    list_editable = ["is_active", "location"]

    fieldsets = (
        (None, {"fields": ("title", "message", "banner_type")}),
        (
            "Location",
            {
                "fields": ("location",),
                "description": 'Select which pages this banner should appear. Choose "global" to show it on all pages.',
            },
        ),
        (
            "Duration",
            {"fields": ("start_date", "end_date", "is_active"), "description": "Control when the banner is displayed"},
        ),
    )

    def title_display(self, obj):
        return obj.title or None

    title_display.short_description = "Title"

    def message_preview(self, obj):
        max_length = 150  # arbitrary
        if len(obj.message) > max_length:
            return f"{obj.message[:max_length]}..."
        return obj.message

    message_preview.short_description = "Message"

    def status_display(self, obj):
        """Display the current status of the banner."""
        now = timezone.now()
        if not obj.is_active:
            return "Inactive"
        elif obj.end_date <= now:
            return "Expired"
        elif obj.start_date > now:
            return "Future"
        else:
            return "Active"

    status_display.short_description = "Status"
