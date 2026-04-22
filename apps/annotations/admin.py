from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin

from .models import CustomTaggedItem, Tag


@admin.register(Tag)
class TagAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "team", "created_at", "updated_at")
    search_fields = ("name",)


@admin.register(CustomTaggedItem)
class CustomTaggedItemAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("user", "team")
    search_fields = ("user__name",)
