from django.contrib import admin
from rest_framework_api_key.admin import APIKeyModelAdmin

from .models import UserAPIKey


@admin.register(UserAPIKey)
class UserAPIKeyModelAdmin(APIKeyModelAdmin):
    list_display = [
        "prefix",
        "team",
        "user",
        "name",
        "created",
        "expiry_date",
        "_has_expired",
        "revoked",
        "read_only"
    ]
    list_filter = ["created", "revoked", "read_only"]
    search_fields = ["name", "prefix", "team__name", "user__username"]
