from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin

from .models import Annotation, AnnotationItem, AnnotationQueue, AnnotationSchema


@admin.register(AnnotationSchema)
class AnnotationSchemaAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "team", "created_at")
    list_filter = ("team",)
    search_fields = ("name",)


@admin.register(AnnotationQueue)
class AnnotationQueueAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "team", "schema", "status", "num_reviews_required", "created_at")
    list_filter = ("team", "status")
    search_fields = ("name",)


@admin.register(AnnotationItem)
class AnnotationItemAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "queue", "item_type", "status", "review_count", "created_at")
    list_filter = ("status", "item_type")


@admin.register(Annotation)
class AnnotationAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "item", "reviewer", "status", "created_at")
    list_filter = ("status",)
