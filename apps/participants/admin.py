from django.contrib import admin

from apps.participants import models


class ParticipantDataInline(admin.TabularInline):
    model = models.ParticipantData


@admin.register(models.Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("identifier", "team", "public_id")
    readonly_fields = ("public_id",)
    list_filter = ("team",)
    search_fields = ("external_chat_id",)
    inlines = [ParticipantDataInline]


@admin.register(models.ParticipantData)
class ParticipantData(admin.ModelAdmin):
    list_display = ("participant", "content_type", "object_id")
    list_filter = ("participant",)
