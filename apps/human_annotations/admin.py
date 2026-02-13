from django.contrib import admin

from .models import AnnotationQueue, AnnotationSchema


@admin.register(AnnotationSchema)
class AnnotationSchemaAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "created_at")
    list_filter = ("team",)
    search_fields = ("name",)


@admin.register(AnnotationQueue)
class AnnotationQueueAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "schema", "status", "num_reviews_required", "created_at")
    list_filter = ("team", "status")
    search_fields = ("name",)
