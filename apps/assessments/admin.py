from django.contrib import admin

from apps.assessments.models import Score
from apps.utils.admin import ReadonlyAdminMixin


@admin.register(Score)
class ScoreAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "team", "source", "name", "data_type", "value_numeric", "value_string", "created_at")
    list_filter = ("source", "data_type", "team")
    search_fields = ("name",)

    def has_add_permission(self, request):
        return False
