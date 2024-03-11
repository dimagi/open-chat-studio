from django.contrib import admin

from .models import CustomTaggedItem, Tag


@admin.register(Tag)
class ChatAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "created_at", "updated_at")
    search_fields = ("name",)


@admin.register(CustomTaggedItem)
class CustomTaggedItemAdmin(admin.ModelAdmin):
    list_display = ("user", "team")
    search_fields = ("user__name",)
