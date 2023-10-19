from django.contrib import admin

from apps.services.models import ServiceConfig


@admin.register(ServiceConfig)
class ServiceConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "service_type", "subtype")
    list_filter = ("team", "service_type", "subtype")
