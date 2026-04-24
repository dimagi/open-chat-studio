from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin

from .models import File


@admin.register(File)
class FileAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "external_source", "external_id", "content_size", "content_type")
    search_fields = ("name", "external_source", "external_id")
    list_filter = ("content_type",)
