from django.contrib import admin

from .models import AnnotationSchema


@admin.register(AnnotationSchema)
class AnnotationSchemaAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "created_at")
    list_filter = ("team",)
    search_fields = ("name",)
