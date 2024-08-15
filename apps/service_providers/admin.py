from django.contrib import admin

from .models import LlmProvider, MessagingProvider, TraceProvider, VoiceProvider


@admin.register(LlmProvider)
class ServiceConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")


@admin.register(VoiceProvider)
class VoiceProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")


@admin.register(MessagingProvider)
class MessagingProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")


@admin.register(TraceProvider)
class TraceProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")
