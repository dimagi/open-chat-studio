from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin
from apps.web.models import SuperuserElevation


@admin.register(SuperuserElevation)
class SuperuserElevationAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("user", "grant", "granted_at", "expires_at", "released_at", "ip")
    list_filter = ("granted_at",)
    search_fields = ("user__email", "grant")
    raw_id_fields = ("user",)
    ordering = ("-granted_at",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
