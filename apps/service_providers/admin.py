from django.contrib import admin

from .models import LlmProvider


@admin.register(LlmProvider)
class ServiceConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")
