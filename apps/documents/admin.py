from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin

from .models import Collection, CollectionFile


class CollectionFileInline(ReadonlyAdminMixin, admin.TabularInline):
    model = CollectionFile
    extra = 0
    exclude = ["team"]


class CollectionVersionsInline(ReadonlyAdminMixin, admin.TabularInline):
    model = Collection
    extra = 0
    exclude = ["team"]


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ("team", "name", "is_index", "created_at")
    search_fields = ("name",)
    list_filter = ("team", "is_index")
    inlines = [CollectionFileInline, CollectionVersionsInline]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.filter(working_version_id__isnull=True)
